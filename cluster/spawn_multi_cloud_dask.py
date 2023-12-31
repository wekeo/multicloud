#!/usr/bin/env python3

"""
script to start a dask scheduler and wireguard gateway
connect to remote clouds and spawn gateways and workers there, connecting back
to the scheduler and to each other

the VPN comes up with a standard config, including a
hard coded, universal IP for the scheduler within the wireguard network
DASK_MESH_NETWORK = "fda5:c0ff:eeee:{site_id}::{local address}"
 - site id 0 = scheduler
 - local address:  1 = router, 11+ = worker id on that site
"""

import argparse
import collections
import subprocess
import random
import string
import sys
import textwrap
import time


# this is a template of the docker compose file we'll create on any machine we ssh to, in order to spawn workers
SITE_COMPOSE_TEMPLATE = """
# Dask router and workers compose file
version: "3"

# network for communication between workers and worker-router
networks:
  dask-cluster:
#    name: dask-cluster

services:

  # the router is the gateway to the mesh network between peers/clouds
  # and the local network to the workers on the peer/cloud
  router:
    restart: no
    image: registry.apps.eo4eu.eu/european-weather-cloud/dask-bids/worker-router-composeported:latest
    command: /usr/local/bin/wg_cloud_gateway.sh
    networks:
      - dask-cluster
    cap_add:
      # privs needed for IPv6 and wireguard kernel settings/config. TODO: switch to userspace..
      - NET_ADMIN
      - SYS_MODULE
    sysctls:
      # enable ipv6
      - net.ipv6.conf.all.disable_ipv6=0
      # enable routing - wanted to do this per network, but we seem to need to turn it on generally
      - net.ipv6.conf.all.forwarding=1
    ports:
      # this is the port we're going to use for the router, coming from the spawner.  Might be good to detect a working port first, if that's possible.
      - "{router_port}:{router_port}/udp"
    environment:
      # This magic string is the basis for the wireguard network used for this subcluster
      # the router and workers can use this to make up their own wireguard keys in a way
      # that they can guess one-another's keys
      CLUSTER_SECRET: {cluster_secret}
      SCHED_ENDPOINT: {sched_ip}:{sched_port}
      WG_SITE_ID: {site_id}
      MAX_WORKERS: {num_workers}
      WG_IPV6_PREFIX: "fda5:c0ff:eeee"
      WG_PORT: {router_port}
      POOL_NAME: {pool_name}
      PROJ_NAME: {proj_name}
      WIREGUARD_PEERS: |
        {peers_wireguard}

  worker:
    image: registry.apps.eo4eu.eu/european-weather-cloud/dask-bids/notebook-composeported:latest
    entrypoint: /usr/local/bin/worker_with_wireguard.sh
    restart: no
    networks:
      - dask-cluster
    cap_add:
      - NET_ADMIN
    sysctls:
      - net.ipv6.conf.all.disable_ipv6=0
    environment:
      # This magic string is the basis for the wireguard network used for this subcluster
      # the router and workers can use this to make up their own wireguard keys in a way
      # that they can guess one-another
      CLUSTER_SECRET: {cluster_secret}
      WG_SITE_ID: {site_id}
      # permanently hard coded scheduler IP and port
      WG_IPV6_PREFIX: "fda5:c0ff:eeee"
      WG_PORT: {router_port}
      POOL_NAME: {pool_name}
      PROJ_NAME: {proj_name}
"""

# a structure we'll use below
CloudConfig = collections.namedtuple('CloudConfig',
                                     ['name', 'username', 'privkey', 'pubkey', 'pool_name', 'site_id', 'endpoint_ip', 'endpoint_port', 'num_workers']
                                     )

#################################

def wireguard_keypair(secret, pool_name, site_id, name):
    """
    wireguard key generation
    args:
     - universal secret for this cluster
     - pool name
     - site id (0-N)
     - name of the thing you want a key for
    return a wireguard key pair (private, public)
    """
     # we do this in an ugly shell pipeline instead of pythonic as we want something identical to the bash output in other scripts, and it's easier to be sure this way
    privkey = subprocess.run(f"echo {secret} {pool_name} {site_id} {name} | md5sum | cut -f 1 -d ' ' | base64 | sed 's/.$/=/'", stdout=subprocess.PIPE, shell=True, check=True, encoding="utf-8").stdout.strip()
    pubkey = subprocess.run(["wg", "pubkey"], input=privkey,stdout=subprocess.PIPE, check=True, encoding="utf-8").stdout.strip()
    return privkey, pubkey


def create_wg_configs(for_site_num, configs):
    """
    produce a wireguard config for a specific site
    this should have the site indicated as the local wireguard interface
    and peer configs for all the other sites
    """

    # first part is for this specific host (privkey, etc)
    wg_conf = textwrap.dedent(f"""
        [Interface]
        PrivateKey = {configs[for_site_num].privkey}
        Address = fda5:c0ff:eeee:{for_site_num}::1/64
        ListenPort = {configs[for_site_num].endpoint_port}
        """)
    # second is all the peer configs (pubkeys, etc)
    for config in configs:
        if config.site_id == for_site_num:
            # skip the site that this config is for (we only want peers here)
            continue
        # add a peer config for each other site
        wg_conf += textwrap.dedent(f"""

            # config for cloud {config.name}
            [Peer]
            PublicKey = {config.pubkey}
            AllowedIPs = fda5:c0ff:eeee:{config.site_id}::0/64
            PersistentKeepalive = 25
            Endpoint = {config.endpoint_ip}:{config.endpoint_port}""")
    return wg_conf


class SSHDockerComposeRemote:
    """
    class for starting routers and workers on a remote cloud, using ssh to push a docker compose config and running that
    """

    def __init__(self, config: CloudConfig, proj_name, cluster_secret):
        self.config = config
        self.proj_name = proj_name
        self.cluster_secret = cluster_secret

    def start_workers(self, all_configs):
        """
        SSH to the remote control node and start up a gateway there using docker.

        Nodes are pre-configured with passwordless ssh auth, docker access, and WireGuard kernel modules
        """

        params = {
            "cluster_secret": self.cluster_secret,
            "sched_ip": all_configs[0].endpoint_ip,
            "sched_port": all_configs[0].endpoint_port,
            "router_port": self.config.endpoint_port,
            "site_id": self.config.site_id,
            "num_workers": self.config.num_workers,
            "pool_name": self.config.pool_name,
            "proj_name": self.proj_name,
            "peers_wireguard": textwrap.indent(create_wg_configs(self.config.site_id, all_configs), '        ')
            }

        subprocess.run(
            f"ssh -o StrictHostKeyChecking=accept-new {self.config.username}@{self.config.endpoint_ip} ".split(" ") + [
                f"TMPDOCK=$(mktemp -d) ; mkdir -p $TMPDOCK/{self.proj_name} ; cd $TMPDOCK/{self.proj_name} ;" +
                "cat > docker-compose.yml ;" +
                "docker compose pull --quiet ; " +
                f"docker compose up -d --scale worker={self.config.num_workers};" +
                "rm -rf $TMPDOCK"],
            check=True,
            input=SITE_COMPOSE_TEMPLATE.format(**params).encode("utf8"),
            stderr=subprocess.STDOUT
        )

    def kill_workers(self):
        """
        ssh to the host and try to bring down any containers running there that we were responsible for
        """
        params = {
            "cluster_secret": self.cluster_secret,
            "sched_ip": "doesn't matter for kills",
            "sched_port": 51820,
            "router_port": self.config.endpoint_port,
            "site_id": self.config.site_id,
            "num_workers": self.config.num_workers,
            "pool_name": self.config.pool_name,
            "proj_name": self.proj_name,
            "peers_wireguard": textwrap.indent("n/a - just for killing", '        ')
            }

        subprocess.run(
            f"ssh -o StrictHostKeyChecking=accept-new {self.config.username}@{self.config.endpoint_ip}".split(" ") + [
                f"TMPDOCK=$(mktemp -d) ; mkdir -p $TMPDOCK/{self.proj_name} ; cd $TMPDOCK/{self.proj_name} ;" +
                "cat > docker-compose.yml ;" +
                "docker compose down ;"+
                "rm -rf $TMPDOCK"],
            check=True,
            input=SITE_COMPOSE_TEMPLATE.format(**params).encode("utf8"),
            stderr=subprocess.STDOUT
        )


def spawn_dask_cluster(
        users_at_hosts,
        wireguard_port,
        # this is the naming that will be pre-pended by docker compose to all containers.
        # It may be useful to add a userid when we have one, for traceability but also for putting multiple container groups on single machines
        proj_name="daskcluster"
):
    """
    Starts a wireguard interface, a scheduler, and workers on remote machines
    """

    # creates a set of CloudConfig structures for all the clouds we've been given
    def generate_configs(clouds, cluster_secret):
        return [config_for(cloud, site_counter, cluster_secret) for site_counter, cloud in enumerate(clouds)]

    # creates a CloudConfig structure for a cloud, creating wireguard keys, etc
    def config_for(cloud, site_id, cluster_secret):
        try:
            pool_name, username, endpoint_ip = cloud.split("@")
            endpoint_port = wireguard_port  # currently we're using the same port for all machines, but we could change this here
            # the number of independent worker containers we'll create per site
            # dask defaults to 1 worker per core, per container, so you'll get two workers for a dual-core machine
            # consider making this an argument, ideally per site
            num_workers = 1
        except ValueError:
            print("cloud names must be of the form POOLNAME@USERID@MACHINE", file=sys.stderr)
            sys.exit(4)
        # in the mesh, site 0 is the scheduler, everything else is a router
        if site_id == 0:
            priv, pub = wireguard_keypair(cluster_secret, pool_name, site_id, "scheduler")
        else:
            priv, pub = wireguard_keypair(cluster_secret, pool_name, site_id, "router")
        return CloudConfig(name=cloud, username=username, privkey=priv, pubkey=pub, site_id=site_id, pool_name=pool_name, endpoint_ip=endpoint_ip, endpoint_port=endpoint_port, num_workers=num_workers)

    # the main bit of main
    try:
        # This magic string is the basis for the wireguard network used for this subcluster
        # the router and workers can use this to make up their own wireguard keys in a way
        # that they can guess one another's keys
        # Generate a fresh one every new run
        cluster_secret = ''.join(random.choice(string.ascii_letters) for i in range(32))

        # detect our own IP (cheesy, might work 100%)
        my_ip = subprocess.check_output(["curl", "--silent", "ifconfig.co"]).decode("utf-8").strip()
        print(f"Detected public IP is {my_ip}")
        # add the scheduler as cloud 0
        users_at_hosts = [f"SCHED@scheduler@{my_ip}"] + users_at_hosts
        # get it and all the other user-specified sites into a nice structure
        configs = generate_configs(users_at_hosts, cluster_secret)

        # get rid of any pre-existing wireguard, then bring up our new one
        conf_file = "/etc/wireguard/dasklocal.conf"
        subprocess.run("sudo wg-quick down dasklocal".split(" "), stdout=None, stderr=None, check=False)
        with open(conf_file, "w") as wgconfig:
            print(create_wg_configs(0, configs), file=wgconfig)
        subprocess.run("sudo wg-quick up dasklocal".split(" "), stdout=None, stderr=None, check=True)
        time.sleep(1) # give it a sec to come up
        # do we need to do this, as there shouldn't be anything behind the scheduler - trying it without
        # subprocess.run("ip6tables -A FORWARD -i dasklocal --jump ACCEPT".split(" "), check=False)

        # get rid of any pre-existing scheduler and start a new one
        print("Starting the scheduler (takes ~5 secs)")
        scheduler = subprocess.run(["killall", "dask"], stdout=None, stderr=None, check=False)
        scheduler = subprocess.Popen(["dask", "scheduler"], stdout=sys.stdout, stderr=subprocess.STDOUT)
        time.sleep(5)  # wait for it to come up

        # prepare remotes structure for all the sites
        remotes = [SSHDockerComposeRemote(config, proj_name, cluster_secret) for config in configs[1:]]

        for remote in remotes:
            print("Killing any pre-existing workers/routers at", remote.config.endpoint_ip)
            remote.kill_workers()
            print("Spawning workers for pool", remote.config.pool_name, "at", remote.config.endpoint_ip)
            remote.start_workers(configs)

        # theoretically everything is now up, so hand over to the scheduler and wait for it to quit
        try:
            print("""

-------------------------------------------------------------------------

All preparations complete - workers should shortly join this scheduler.

To quit cleanly, press ctrl-C once only!

-------------------------------------------------------------------------

""")
            scheduler.communicate()
        except KeyboardInterrupt:
            pass  # don't die yet, do the shutdown instead
            # consider better catching mechanism, this only gets ctrl-c, and only once

        print("Scheduler finished; killing remote workers/routers")
        for remote in remotes:
            remote.kill_workers()

    except subprocess.CalledProcessError as exception:
        print(f"Subprocess output for {exception} was {exception.output}")
        raise exception

    # and exit..


def main():
    """
    Simple main routine
    """

    parser = argparse.ArgumentParser(description='Start up dask cluster on multiple machines, using a wireguard VPN for communication')
    parser.add_argument("--cluster_name", "-n", required=True, help="a unique name to identify this cluster")
    parser.add_argument("--port", "-p", required=True, type=int, default=51820, choices=range(51820, 51841), help="port number the wireguard network should use everywhere (51820-51840, because ECMWF firewall limits this)")
    parser.add_argument("hosts", nargs="+", help="hosts expressed as pool_name@userid@host for connecting to using ssh, e.g. EUM@dasktest@64.225.133.132")

    args = parser.parse_args()

    spawn_dask_cluster(args.hosts, args.port, proj_name=args.cluster_name)

if __name__ == '__main__':
    main()
