import os

import pkg_resources
from setuptools import find_packages, setup

setup(
    name="safim",
    py_modules=["safim"],
    version="1.0",
    description="",
    author="https://github.com/gonglinyuan/safim",
    packages=find_packages(),
    install_requires=[
        str(r)
        for r in pkg_resources.parse_requirements(
            open(os.path.join(os.path.dirname(__file__), "requirements.txt"))
        )
    ]
)