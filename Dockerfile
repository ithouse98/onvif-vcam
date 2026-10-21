FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY vcam/ /app/vcam/
VOLUME ["/videos", "/state"]
# WS-Discovery is multicast: run with  --network host  so VMS discovery works.
EXPOSE 8080 8554 3702/udp
ENTRYPOINT ["python", "-m", "vcam", "--state-dir", "/state"]
CMD ["/videos/sample.mp4"]
