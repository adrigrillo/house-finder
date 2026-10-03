#!/bin/bash

# =======================================
# Entrypoint to run any docker as host user
#   - Only use the script when base user inside docker is root.
#
# Version: 1.0.1
#   - Skip host user creation if missing ENVs or running in ROOTLESS mode.
#   - ENVS to run the script:
#         - U_ID is the output of host `id -u`.
#         - G_ID is the output of host `id -g`.
#         - HOST_USER is the output of host `whoami`.
#         - IS_ROOTLESS flag that indicates if docker is rootless (1) or not (0).
#   - For the IS_ROOTLESS we advise use the following command:
#       `docker info -f "{{println .SecurityOptions}}" | grep rootless > /dev/null 2>&1 && echo 1 || echo 0`
#   - Added support for Ubuntu-based systems to preserve the PATH variable when switching users.
# =======================================

# Check if the script is run as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

if [ -z "$U_ID" ] || [ -z "$G_ID" ] || [ -z "$HOST_USER" ] || [ -z "$IS_ROOTLESS" ]; then
  echo "[$(basename "$0")] Some missing ENV values, skipping host user creation"
  exec bash -c "${*:-bash}"
elif [[ $IS_ROOTLESS -ne 0 ]]; then
  echo "[$(basename "$0")] Docker is ROOTLESS, skipping host user creation"
  exec bash -c "${*:-bash}"
else
  echo "[$(basename "$0")] Creating host user and switching to host user"
  # Create the group if it doesn't exist
  if ! getent group "$G_ID" >/dev/null; then
    groupadd -g "$G_ID" "$HOST_USER" >/dev/null 2>&1
  fi
  # Create the user if it doesn't exist
  if ! id -u "$HOST_USER" >/dev/null 2>&1; then
    useradd -u "$U_ID" -g "$G_ID" -m "$HOST_USER" >/dev/null 2>&1
    find /home/"$HOST_USER" -maxdepth 1 -exec chown "$HOST_USER":"$HOST_USER" {} \;
    # Add /etc/skel files in case home directory already exists on useradd
    install -m 644 -o "$U_ID" -g "$G_ID" /etc/skel/.* /home/"$HOST_USER"/
  fi

  # Save the current PATH to ensure it's preserved when switching user
  # Check if the system is Ubuntu based (excluding other Debian-based systems)
  if [ -f /etc/os-release ] && grep -q "Ubuntu" /etc/os-release; then
    echo "[$(basename "$0")] Ubuntu-based system detected, preserving PATH"
    # Switch to the new user and terminate the script, preserving PATH
    CURRENT_PATH="$PATH"
    exec su "$HOST_USER" -m -P -c "export PATH='$CURRENT_PATH':\$PATH; ${*:-bash}"
  else
    # On other systems (including Debian), use the standard approach
    exec su "$HOST_USER" -m -P -c "${*:-bash}"
  fi
fi
