FROM alpine:3.20

RUN apk add --no-cache nftables python3 py3-yaml tzdata bash iproute2

COPY reconcile.py /usr/local/bin/reconcile.py
RUN chmod +x /usr/local/bin/reconcile.py

# Run forever, reconciling every minute.
CMD ["/usr/bin/python3", "/usr/local/bin/reconcile.py"]
