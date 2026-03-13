"""
NexSync setup.py
Installs nexsync as a system-wide CLI command.
Run: pip install -e .
Then use: nexsync init / nexsync push / nexsync pull
"""

from setuptools import setup, find_packages

setup(
    name="nexsync",
    version="1.0.0",
    description="Git-like cross-platform file sync for Mac and Windows",
    author="Your Name",
    packages=find_packages(),
    install_requires=[
        "gitpython>=3.1.40",
        "watchdog>=3.0.0",
        "paramiko>=3.3.1",
        "zeroconf>=0.115.0",
        "click>=8.1.7",
        "flask>=3.0.0",
        "pystray>=0.19.4",
        "Pillow>=10.0.0",
    ],
    entry_points={
        "console_scripts": [
            # This makes `nexsync` available as a terminal command
            "nexsync=cli.commands:CLI",
        ],
    },
    python_requires=">=3.9",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: System :: Filesystems",
        "Topic :: Utilities",
    ],
)
