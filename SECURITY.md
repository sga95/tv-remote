# Security policy

## Reporting a vulnerability

Please report it privately: on the repository page open **Security > Report a vulnerability** (GitHub private vulnerability reporting). Do not open a public issue for a security problem.

Include: what you found, the steps to reproduce it, the version (the SHA-256 of `remote_fast.py` or the release name) and what you expected. Never include real secrets or home-network addresses.

This is a small project kept by one person: reports are read on a best-effort basis, with no guaranteed response time and no bounty. A confirmed problem is fixed in a new release and credited if you wish.

## What is in scope

The code in this repository: the server (`remote_fast.py`), the guided setup (`setup.py`) and the package builder (`make_package.py`). In particular anything that lets a device without the secret or the paired cookie control the TV, anything that makes the program contact a host other than the TV, and anything that leaks a secret.

## What is not in scope

Android Debug Bridge (a Google program), Windows, the TV and the phone's browser, and any problem that needs an attacker who already controls the computer or an unlocked paired phone. The known limits are listed in the README.

## Supported versions

Only the latest release.
