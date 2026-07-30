
v4l2-ctl -d /dev/video0 --set-fmt-video=width=640,height=480,pixelformat=UYVY --stream-mmap
media-ctl -d /dev/media0 --set-v4l2 '"sun6i-csi-bridge":0[fmt:UYVY8_2X8/640x480]'
v4l2-ctl -d /dev/video0 --set-fmt-video=width=640,height=480,pixelformat=UYVY --stream-mmap
