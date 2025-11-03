#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/concourse/plays/uninstall_concourse.yml
