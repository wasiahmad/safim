import os
from setuptools import find_packages, setup

_REQUIREMENTS_PATH = os.path.join(os.path.dirname(__file__), "requirements.txt")
with open(_REQUIREMENTS_PATH, encoding="utf-8") as rf:
    _install_requires = [
        line.strip()
        for line in rf
        if line.strip() and not line.strip().startswith("#")
    ]

setup(
    name="safim",
    version="1.0",
    description="",
    author="https://github.com/gonglinyuan/safim",
    packages=find_packages(),
    install_requires=_install_requires,
)
