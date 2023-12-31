import json
import os
import s3fs
import xarray as xr
import intake
import ipywidgets as widgets
import boto3
import numpy as np
from datetime import datetime
import pandas as pd
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
from cartopy.mpl.geoaxes import GeoAxes

from matplotlib import pyplot as plt
from IPython.display import HTML

def read_credentials(auth_file='.cloud_credentials'):
    '''    
    This function reads authentication details for our cloud systems. It should have the following format:
    
    {
    "EWC": ["<key>", "<secret>"],
    "WEkEO": ["<key>", "<secret>"]
    }

    Note that the files should have no extension (e.g. do not add .txt). You should replace the contents of the <> with the relevant
    details.

    auth_file: the credentials file you wish to read, with full path
    '''
    with open(auth_file) as json_file:
        credentials = json.load(json_file)
    return credentials

def get_urls(s3_endpoint, aws_access_key_id, aws_secret_access_key, bucket_name, prefix, url_adaptor):

    session = boto3.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key)

    s3_client = session.client(
        's3',
        endpoint_url=s3_endpoint,
        region_name='us-west-1'  # This can be any value if your endpoint isn't AWS
    )

    endpoint_url = s3_endpoint + url_adaptor

    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

    urls = []

    for page in pages:
        for obj in page['Contents']:
            if obj['Key'].endswith('.tif'):
                urls.append(endpoint_url + bucket_name + '/' + obj['Key'])
    return urls

def load_cogs(urls, time_coords, conversion_factor=1):
    sources = intake.open_rasterio(urls, chunks="auto", concat_dim='band')

    array = sources.to_dask()

    array = array.rename({'x': 'longitude', 'y':'latitude', 'band':'time'}).assign_coords(time=time_coords)
    array = array*conversion_factor
    array = array.where(array >= 0, np.nan)
    return array

def get_time_coords(urls, start, end):
    time_coords = []
    for i in range(0,len(urls)):
        time = datetime.strptime(urls[i][start:end], '%Y%m%d')
        time_coords.append(time)

    return pd.DatetimeIndex(time_coords)

def visualize_pcolormesh(data_array, longitude, latitude, projection, color_scale, unit, long_name, vmin, vmax,
                        set_global=True, lonmin=-180, lonmax=180, latmin=-90, latmax=90):
    """ 
    Visualizes a xarray.DataArray with matplotlib's pcolormesh function.
    
    Parameters:
        data_array(xarray.DataArray): xarray.DataArray holding the data values
        longitude(xarray.DataArray): xarray.DataArray holding the longitude values
        latitude(xarray.DataArray): xarray.DataArray holding the latitude values
        projection(str): a projection provided by the cartopy library, e.g. ccrs.PlateCarree()
        color_scale(str): string taken from matplotlib's color ramp reference
        unit(str): the unit of the parameter, taken from the NetCDF file if possible
        long_name(str): long name of the parameter, taken from the NetCDF file if possible
        vmin(int): minimum number on visualisation legend
        vmax(int): maximum number on visualisation legend
        set_global(boolean): optional kwarg, default is True
        lonmin,lonmax,latmin,latmax(float): optional kwarg, set geographic extent is set_global kwarg is set to 
                                            False

    """
    fig=plt.figure(figsize=(20, 10))

    ax = plt.axes(projection=projection)
   
    img = plt.pcolormesh(longitude, latitude, data_array, 
                        cmap=plt.get_cmap(color_scale), transform=ccrs.PlateCarree(),
                        vmin=vmin,
                        vmax=vmax,
                        shading='auto')

    ax.add_feature(cfeature.BORDERS, edgecolor='black', linewidth=1)
    ax.add_feature(cfeature.COASTLINE, edgecolor='black', linewidth=1)

    if (projection==ccrs.PlateCarree()):
        ax.set_extent([lonmin, lonmax, latmin, latmax], projection)
        gl = ax.gridlines(draw_labels=True, linestyle='--')
        gl.top_labels=False
        gl.right_labels=False
        gl.xformatter=LONGITUDE_FORMATTER
        gl.yformatter=LATITUDE_FORMATTER
        gl.xlabel_style={'size':14}
        gl.ylabel_style={'size':14}

    if(set_global):
        ax.set_global()
        ax.gridlines()

    cbar = fig.colorbar(img, ax=ax, orientation='horizontal', fraction=0.04, pad=0.1)
    cbar.set_label(unit, fontsize=12)
    cbar.ax.tick_params(labelsize=12)
    ax.set_title(long_name, fontsize=16, pad=20.0)

 #   plt.show()
    return fig, ax
    

def auth_widget(auth_file):
    '''
    Function to set up a widget to allow population of an authentication file

    auth_file: the authentication file you wish to create, with full path.
    '''
    layout = widgets.Layout(width='100', height='40px')

    box1 = widgets.Text(
        value=None,
        placeholder='Enter your EWC key',
        disabled=False,
        layout=layout,
        display='flex'
    )
    box2 = widgets.Password(
        value='',
        placeholder='Enter your EWC secret',
        disabled=False
    )
    
    box3 = widgets.Text(
        value=None,
        placeholder='Enter your WEkEO key',
        disabled=False,
        layout=layout,
        display='flex'
    )
    box4 = widgets.Password(
        value='',
        placeholder='Enter your WEkEO secret',
        disabled=False
    )
    
    button = widgets.Button(
        description='Create auth file',
        disabled=False,
        button_style='info',
        tooltip='Click me',
        icon='file'
    )

    output = widgets.Output()
    display(widgets.VBox([widgets.HBox([box1, box2]), widgets.HBox([box3, box4]), button]), output)

    def on_button_clicked(b):
        with output:
            out_string = '{{\n"EWC": ["{box1}", "{box2}"],\n"WEKEO": ["{box3}", "{box4}"]\n}}'
            out_string = out_string.format(box1 = box1.value, box2 = box2.value, box3 = box3.value, box4 = box4.value)

            try:
                os.remove(auth_file)
            except OSError:
                pass

            with open(auth_file, "w") as f:
                f.write(out_string)
                print(f"{auth_file} created")

    button.on_click(on_button_clicked)
