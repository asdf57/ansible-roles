#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/kube/plays/uninstall.yml -l $1
