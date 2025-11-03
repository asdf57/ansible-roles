ARG CONCOURSE_VERSION
FROM concourse/concourse:${CONCOURSE_VERSION}

COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]