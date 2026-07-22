from setuptools import setup, find_packages

setup(
    name="audisor_backend",
    version="0.9.0",
    package_dir={"audisor_backend": "."},
    packages=["audisor_backend"] + ["audisor_backend." + p for p in find_packages(where=".") if p != "audisor_backend"],
)
