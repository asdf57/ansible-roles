#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/cert-manager/plays/install_cert_manager.yml
