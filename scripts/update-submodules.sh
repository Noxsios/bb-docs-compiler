#! /usr/bin/env bash

set -eu 

echo "👉 Updating all submodules"
git submodule update --init --recursive --jobs 8  &> /dev/null