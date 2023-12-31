import json
import os
import s3fs
import xarray as xr
import intake
import ipywidgets as widgets

def read_credentials(auth_file='.cloud_credentials'):
    '''    
    This function reads authentication details for our cloud systems. It should have the following format:
    
    {
    "EWC": ["<key>", "<secret>"],
    "WEKEO": ["<key>", "<secret>"]
    }

    Note that the files should have no extension (e.g. do not add .txt). You should replace the contents of the <> with the relevant
    details.

    auth_file: the credentials file you wish to read, with full path
    '''
    with open(auth_file) as json_file:
        credentials = json.load(json_file)
        return credentials

def read_ZARR_data_S3(endpoint_url, key, secret, s3_path):
    '''
    Quick function to read ZARR objects from remote storage into xarray dataset

    endpoint_url : the endpoint url for the data source
    key : the key for access 
    secret : the secret for access
    s3_path : the s3 path for the data you wish to access
    '''
    s3 = s3fs.S3FileSystem(anon=False, client_kwargs={'endpoint_url': 
            endpoint_url}, key=key, secret=secret)
    data = xr.open_zarr(store=s3fs.S3Map(root=s3_path, s3=s3, check=False), chunks="auto", consolidated=True)    
    return data

def read_CoG_data_S3(endpoint_url, key, secret, s3_path, url_adaptor=None, search_terms=None):
    '''
    Quick function to read CoG objects from remote storage into xarray dataset using intake

    endpoint_url : the endpoint url for the data source
    key : the key for access 
    secret : the secret for access
    s3_path : the s3 path for the data you wish to access
    url_adaptor : any url subpaths to be appened to the end_point url
    search_terms : [list] of terms to refine the search by
    '''
    s3 = s3fs.S3FileSystem(anon=False, client_kwargs={'endpoint_url': 
            endpoint_url}, key=key, secret=secret)
    objects = s3.ls(s3_path)

    if url_adaptor:
        endpoint_url = endpoint_url + url_adaptor

    if search_terms:
        image_urls = [endpoint_url + obj for obj in objects if any(search_term in obj for search_term in search_terms)]
    else:
        image_urls = [endpoint_url + obj for obj in objects]

    sources = intake.open_rasterio(image_urls, chunks="auto", concat_dim='band')
    data = sources.to_dask()

    return data, image_urls

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
