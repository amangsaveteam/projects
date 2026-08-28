# Pico upperlimb common dependencies

This carrier supplies the system libraries required by the Pico upperlimb
runtime but absent from its input Debian packages:

- `libzmq5` for `libzmq.so.5`;
- `libhdf5-cpp-103` for `libhdf5_cpp.so.103`;
- `libhdf5-103` for `libhdf5_serial.so.103`.

The build resolves the complete Focal/amd64 APT dependency closure into the
carrier.  On the offline Pico, install the carrier and then its payloads:

```bash
sudo dpkg -i navi_pico_upperlimb_common_dep-2.0.0-release-humble-amd64.deb
sudo /usr/sbin/install_pico_upperlimb_common_deps.sh
```

`navi-pico-common-dep` must already be installed.  It provides the shared
Pico device configuration, `/etc/nav01/Middleware.env`, and CycloneDDS
configuration used by the upperlimb runtime.
