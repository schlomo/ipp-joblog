# Alpine's own python3 is enough: ipp-joblog has no runtime dependencies.
FROM alpine:3.22

# tzdata so TZ= resolves: the dashboard shows job times in the printer's own
# offset, and the "updated" stamp has to agree with them.
RUN apk add --no-cache python3 tzdata \
 && adduser -D -H -u 10001 ipp-joblog

COPY src/ /opt/ipp-joblog/

# The image has no git history and no installed package metadata, so the
# version is handed in at build time. See ipp_joblog/version.py.
ARG VERSION=0.0.0+container
LABEL org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.source="https://github.com/schlomo/ipp-joblog" \
      org.opencontainers.image.licenses="MIT"

ENV IPP_JOBLOG_VERSION=$VERSION \
    PYTHONPATH=/opt/ipp-joblog \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IPP_JOBLOG_STATE_DIR=/data \
    IPP_JOBLOG_PORT=8080

RUN install -d -o ipp-joblog -g ipp-joblog /data
VOLUME /data
EXPOSE 8080
USER ipp-joblog

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD wget -qO /dev/null http://127.0.0.1:8080/ || exit 1

ENTRYPOINT ["python3", "-m", "ipp_joblog"]
CMD ["serve"]
