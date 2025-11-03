#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/harbor/plays/uninstall_harbor.yml
