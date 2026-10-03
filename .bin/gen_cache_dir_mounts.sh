#!/bin/bash

# =======================================
# Version: 1.0
# =======================================

help() {
  echo "Utility to generate docker run parameters for cache volume binding."
  echo
  echo "Syntax: $0 [-h|u|b|d]"
  echo
  echo "Options:"
  echo "-h    Print this help."
  echo "-u    (Required) User."
  echo "-b    (Required) Base directory."
  echo "-d    (Required) Comma separated directories."
  echo "-c    Create directories."
  echo
}

split_by() {
  IFS=$1
  read -ra "$2" <<< "$3"
}

function join_by { local IFS="$1"; shift; echo "$*"; }

CREATE=false

options="hu:b:d:c"
while getopts $options option; do
  case "$option" in
  h)
    help
    exit 0
    ;;
  u)
    H_USER=$OPTARG
    ;;
  b)
    BASE_DIR=$OPTARG
    ;;
  d)
    split_by , H_DIRS "$OPTARG"
    ;;
  c)
    CREATE=true
    ;;
  esac
done

if [ ! "$H_USER" ] ; then
  echo "argument -u must be provided"
  exit 1
fi

if [ ! "$BASE_DIR" ] ; then
  echo "argument -b must be provided"
  exit 1
fi

if [ ! "$H_DIRS" ] ; then
  echo "argument -d must be provided"
  exit 1
fi

_BIND_DIRS=()
for cache_dir in "${H_DIRS[@]}"; do
  _DIR="/home/${H_USER}/${BASE_DIR}/${cache_dir}"
  if [ "$CREATE" == true ]; then
    mkdir -p "$_DIR"
  fi
  _BIND_DIRS+=("-v ${_DIR}:/home/${H_USER}/.cache/${cache_dir}")
done

BIND_DIRS="$(join_by " " "${_BIND_DIRS[@]}")"

echo "$BIND_DIRS"
