#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/kube/plays/install.yml -l $1
