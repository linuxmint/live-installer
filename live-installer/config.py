import yaml
import os

def parse_config():
    if os.path.isfile("/etc/live-installer/config.yaml"):
        file = open("/etc/live-installer/config.yaml", "r")
        contents = yaml.load(file, Loader=yaml.FullLoader)

        return contents
    else:
        exit("Config file doesn't exists. Please create config file!")