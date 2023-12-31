#!/bin/sh

USER_WORKER_LIST=/usr/local/share/user_worker_list.txt

usage () {
    echo User id in \$JUPYTERHUB_USER should match a userid in the text file ${USER_WORKER_LIST}
    exit 1
}

USERID=${JUPYTERHUB_USER}

SPAWN_CMD=$(grep --max-count=1 -- "^${USERID} " ${USER_WORKER_LIST} 2> /dev/null)

if [ $? -ne 0 ] ; then
    usage
fi

sudo $(echo ${SPAWN_CMD} | cut -d' ' -f2-)
