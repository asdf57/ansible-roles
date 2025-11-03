#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/cert-manager/plays/uninstall_cert_manager.yml
