#!/bin/sh
set -e
chown -R appuser:appuser /data
exec gosu appuser "$@"
