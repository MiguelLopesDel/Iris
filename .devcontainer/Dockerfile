FROM mcr.microsoft.com/devcontainers/python:3.11

# Install system dependencies for OpenCV and other potential libraries needed by the project
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*