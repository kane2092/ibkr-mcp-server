from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="ibkr-mcp-server",
    version="2.0.0",
    author="IBKR MCP Development",
    description="Interactive Brokers MCP Server for algorithmic trading",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/ibkr-mcp-server",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Financial and Insurance Industry",
        "Topic :: Office/Business :: Financial :: Investment",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.9",
    install_requires=[
        "mcp>=1.0.0",
        "ibapi>=10.19.04",
        "pydantic>=2.0.0",
        "httpx>=0.25.0",
        "aiofiles>=23.0.0",
        "python-dotenv>=1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "ibkr-mcp-server=ibkr_mcp_server:main",
        ],
    },
    project_urls={
        "Bug Reports": "https://github.com/yourusername/ibkr-mcp-server/issues",
        "Source": "https://github.com/yourusername/ibkr-mcp-server",
        "Documentation": "https://github.com/yourusername/ibkr-mcp-server/wiki",
    },
)
