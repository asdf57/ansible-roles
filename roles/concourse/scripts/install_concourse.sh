#!/bin/bash

ansible-playbook -i ansible/inventory/inventory.ini ansible/roles/concourse/plays/install_concourse.yml
