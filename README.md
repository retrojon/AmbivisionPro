# AmbivisionPro

Orange Pi Zero Plus 2 Restore

I have the OV5640 on-board camera working within Linux orangepizeroplus2-h3 5.10.34-sunxi #21.05.1 SMP Thu May 6 20:13:21 UTC 2021 armv7l GNU/Linux

this will setup the camera for hyperion capture:

media-ctl --device /dev/media1 --set-v4l2 '"ov5640 1-003c":0[fmt:YUYV8_2X8/640x480]'
v4l2-ctl -d /dev/video1 --set-fmt-video=width=640,height=480,pixelformat=YUYV
v4l2-ctl -d /dev/video1 --stream-mmap --stream-count=30 --stream-to=/tmp/frame.raw

Toggle USB capture in Hyperion, device:
sun6i-csi

