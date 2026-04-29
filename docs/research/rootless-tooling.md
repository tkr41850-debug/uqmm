# Rootless tooling for image and ISO work

Every tool used by uqmm must work without root. This doc lists what works, what doesn't, and the rootless alternatives.

## Disk image creation

`qemu-img create` — fully rootless.

```sh
qemu-img create -f qcow2 alpine.img 20G
```

- Both `raw` and `qcow2` create as ordinary files.
- Sparse by default when the host filesystem supports holes.
- `preallocation=metadata` is qcow2-only; `falloc` works for raw and qcow2. These are format/space tradeoffs, not privilege differences.
- **Prefer `qcow2`** for snapshots and smaller on-disk size.

## Mounting guest disk read-only — rootless options

`qemu-nbd -c /dev/nbdX` requires `modprobe nbd` plus typically root — **don't use it**.

Rootless alternatives, all from libguestfs:

| Tool | Use case |
|---|---|
| `guestmount -a disk.qcow2 -i --ro /tmp/mnt` | FUSE mount, usually rootless in practice, but depends on host FUSE access. **Recommended for read-only inspection when available.** |
| `guestfish -a disk.qcow2 -i --ro` | Interactive/scriptable shell, no mount needed. Cleanest for reading config files. |
| `nbdfuse` | FUSE-exposes an NBD endpoint; combine with `qemu-nbd --socket=...` for a Unix-socket NBD server (no `/dev/nbd` needed). |
| `libnbd` (Python bindings) | Direct programmatic access (no mount). |

For uqmm, `guestfish` scripted mode is the cleanest fit if we ever need to read state from inside an installed image.

## Building ISOs

All three rootless. **`xorriso` is the most portable** and does not require any privileged setup:

```sh
# CIDATA seed for cloud-init NoCloud (Ubuntu)
xorriso -as mkisofs -o seed.iso -V CIDATA -J -r user-data meta-data
```

`CIDATA` is the conventional spelling in cloud-init docs, but lowercase `cidata` is also accepted and matches this repo's implementation.

```sh
# Alternative: genisoimage
genisoimage -output seed.iso -volid cidata -rational-rock -joliet user-data meta-data
```

`mkisofs` is the historical alias.

## Modifying an existing ISO (Alpine apkovl injection)

```sh
xorriso -indev original.iso \
        -outdev custom.iso \
        -map localhost.apkovl.tar.gz /localhost.apkovl.tar.gz \
        -boot_image any replay
```

`-boot_image any replay` preserves the existing boot configuration (syslinux/isolinux/grub).

## Extracting from an ISO without root

If you need to extract `casper/vmlinuz` and `casper/initrd` from an Ubuntu ISO without `sudo mount -o loop`:

```sh
# Option 1: 7z
7z e ubuntu-24.04-live-server-amd64.iso casper/vmlinuz casper/initrd

# Option 2: xorriso
xorriso -osirrox on \
        -indev ubuntu-24.04-live-server-amd64.iso \
        -extract /casper/vmlinuz vmlinuz \
        -extract /casper/initrd initrd
```

Both rootless. Prefer `xorriso` since it's already a dependency for other ISO ops.

## QEMU attach syntax for seed ISO

```
-drive file=seed.iso,format=raw,if=virtio,readonly=on
```

cloud-init's NoCloud datasource picks it up automatically when the volume is labeled `CIDATA`.

## Building QEMU from source (no-root)

Standard configure/make works as a regular user. Install to a user-writable prefix:

```sh
git clone https://gitlab.com/qemu-project/qemu.git
cd qemu
./configure --prefix="$HOME/.local" --target-list=x86_64-softmmu
make -j"$(nproc)"
make install
```

Add `$HOME/.local/bin` to `PATH`. No `sudo` needed at any step.

## Sources

- [qemu-nbd manual](https://qemu.readthedocs.io/en/master/tools/qemu-nbd.html) — notes root requirement for `-c`
- [guestmount manual](https://libguestfs.org/guestmount.1.html)
- [guestfish manual](https://libguestfs.org/guestfish.1.html)
- [nbdfuse manual](https://libguestfs.org/nbdfuse.1.html)
- [xorriso manual](https://www.gnu.org/software/xorriso/man_1_xorriso.html)
- [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)
- [QEMU build instructions](https://wiki.qemu.org/Hosts/Linux)
