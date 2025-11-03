#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/metallb/plays/install.yml
