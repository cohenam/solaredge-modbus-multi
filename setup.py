from setuptools import find_packages, setup

setup(
    name="solaredge_modbus_multi",
    version="3.3.0",
    packages=find_packages(include=["custom_components*"]),
    install_requires=[
        "modbus-connection[pymodbus]==4.0.0a1",
        "pymodbus==3.13.1",
        "awesomeversion>=25.5.0",
    ],
)
