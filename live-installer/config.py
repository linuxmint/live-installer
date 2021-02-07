import yaml
import os

def load_config(file):
    try:
        contents = yaml.load(file, Loader=yaml.FullLoader)
    except:
        contents = yaml.load(file)
    return contents

def parse_config():
    if os.path.isfile("/etc/live-installer/config.yaml"):
        file = open("/etc/live-installer/config.yaml", "r")
    elif os.path.isfile("configs/config.yaml"):
        file = open("configs/config.yaml", "r")
    else:
        exit("Config file doesn't exists. Please create config file!")
    
    return load_config(file)
