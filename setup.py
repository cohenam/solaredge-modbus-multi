from setuptools import setup, find_packages

setup(
    name="solaredge_modbus_multi",
    version="3.3.0",
    packages=find_packages(include=["custom_components*"]),
    install_requires=[
        "pymodbus>=3.8.3",
    ],
)
