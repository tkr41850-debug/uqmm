"""AlpineSeedBuilder — stock ISO + serial-pexpect path.

See docs/research/alpine-unattended.md for the answer-file schema and
docs/design/config.md § AlpineSeedBuilder for the build/runtime split.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pycdlib

from uqmm.builders.base import InstallArtifacts
from uqmm.config import VMConfig
from uqmm.resolve import resolve_image


def render_answers(cfg: VMConfig) -> str:
    """Render a setup-alpine answer file.

    Trust `setup-alpine -c` output over the wiki when in doubt — the wiki has
    documented spelling inconsistencies. Field set chosen to match a 3.21
    canonical skeleton.
    """
    keys_joined = "\n".join(cfg.ssh_authorized_keys)
    hostname = cfg.effective_hostname()
    # When cfg.user == "root", skip non-root user creation (USEROPTS empty)
    # and route the keys to root via ROOTSSHKEY. setup-alpine -e leaves root
    # with an empty password; sshd's default `PermitRootLogin prohibit-password`
    # blocks password auth over SSH but allows pubkey, so root SSH with key
    # works without further sshd_config edits.
    if cfg.user == "root":
        useropts = ""
        usersshkey = ""
        rootsshkey = keys_joined
    else:
        useropts = f"-a -u -g wheel,audio,video,netdev {cfg.user}"
        usersshkey = keys_joined
        rootsshkey = ""
    # Three non-obvious points:
    # - DNSOPTS must name a resolver. setup-dns rewrites /etc/resolv.conf
    #   from these flags, so an empty value clobbers the one udhcpc set
    #   from the SLiRP DHCP lease — apk's mirror lookup then fails with
    #   "bad address". 10.0.2.3 is SLiRP's built-in resolver.
    # - APKREPOSOPTS must be a fully-qualified repo URL including the
    #   version path. `setup-apkrepos -1` (auto-pick) fetches a mirror list
    #   from `mirrors.alpinelinux.org`, which is flaky from inside SLiRP
    #   (host can resolve it, guest can't). Passing a positional URL also
    #   does NOT cause setup-apkrepos to append `/v$VER/main` despite the
    #   wiki's claim — the URL is written verbatim, so `setup-disk -m sys`
    #   can't find `syslinux`. Spell out the full path ourselves.
    # - USEROPTS values are word-split by the answers-file consumer; embedded
    #   quotes are preserved literally, so `-g 'wheel,…'` passes a group
    #   named `'wheel` to addgroup. The list has no whitespace, so leave it
    #   unquoted.
    repo_url = f"https://dl-cdn.alpinelinux.org/alpine/v{cfg.version}/main"
    return f"""\
KEYMAPOPTS="us us"
HOSTNAMEOPTS="-n {hostname}"
DEVDOPTS=mdev
INTERFACESOPTS="auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
"
DNSOPTS="-n 10.0.2.3"
TIMEZONEOPTS="-z UTC"
PROXYOPTS="none"
APKREPOSOPTS="{repo_url}"
USEROPTS="{useropts}"
USERSSHKEY="{usersshkey}"
ROOTSSHKEY="{rootsshkey}"
SSHDOPTS="-c openssh"
NTPOPTS="-c chrony"
DISKOPTS="-m sys -s 0 /dev/vda"
LBUOPTS="none"
APKCACHEOPTS="none"
"""


def extract_alpine_boot_files(iso: Path) -> tuple[Path, Path]:
    """Extract /boot/vmlinuz-virt and /boot/initramfs-virt from an Alpine virt ISO.

    Cached as siblings of the ISO under `<iso>.boot/`. Returns
    (kernel_path, initrd_path). Idempotent — returns cached paths if
    already extracted.

    Why we extract: the Alpine virt ISO's syslinux/grub cmdline omits
    `console=ttyS0` (the wiki claim that it ships with serial enabled is
    out of date as of 3.21). Without it, OpenRC's serial-getty service
    never starts on ttyS0 and `localhost login:` never appears on the
    pexpect socket. Bypassing isolinux with `-kernel`/`-initrd`/`-append`
    lets us inject the right cmdline directly.
    """
    cache_dir = iso.parent / f"{iso.name}.boot"
    kernel = cache_dir / "vmlinuz-virt"
    initrd = cache_dir / "initramfs-virt"
    if kernel.exists() and initrd.exists():
        return kernel, initrd
    cache_dir.mkdir(parents=True, exist_ok=True)
    iso_obj = pycdlib.PyCdlib()
    iso_obj.open(str(iso))
    try:
        for src, dest in (("/boot/vmlinuz-virt", kernel), ("/boot/initramfs-virt", initrd)):
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            try:
                with tmp.open("wb") as f:
                    iso_obj.get_file_from_iso_fp(f, rr_path=src)
                tmp.replace(dest)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
    finally:
        iso_obj.close()
    return kernel, initrd


def build_disk(disk: Path, size_gb: int) -> None:
    """Create an empty qcow2 disk for setup-alpine to install into.

    Writes to a .tmp sidecar and renames on success so a failed create
    leaves no partial disk behind (R3).
    """
    import os

    tmp = disk.with_suffix(disk.suffix + ".tmp")
    cmd = ["qemu-img", "create", "-f", "qcow2", str(tmp), f"{size_gb}G"]
    try:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            raise RuntimeError(f"qemu-img create failed: {stderr.strip()}") from e
        os.replace(tmp, disk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class AlpineSeedBuilder:
    """Builder for Alpine via stock ISO + serial pexpect.

    Unlike CloudImageBuilder, install and runtime args differ: install boots
    from the ISO with -no-reboot so QEMU exits when setup-alpine triggers a
    reboot; runtime drops the CD and -no-reboot.
    """

    def build(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts:
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before building")

        iso = resolve_image(cfg)
        disk = vm_dir / "disk.qcow2"
        answers = vm_dir / "answers"

        build_disk(disk, size_gb=cfg.disk_size_gb)
        answers.write_text(render_answers(cfg))
        (vm_dir / "state.seeded").touch()

        install = _qemu_install_args(cfg, vm_dir, disk, iso)
        runtime = _qemu_runtime_args(cfg, vm_dir, disk)
        return InstallArtifacts(
            qemu_install_args=install,
            qemu_runtime_args=runtime,
            seed_paths=[disk, answers],
        )

    def rebuild_seed(self, cfg: VMConfig, vm_dir: Path) -> InstallArtifacts:
        """Regenerate answers from current cfg, reuse existing disk.

        Used when resuming from state.seeded — disk exists, install hasn't run.
        Does not call build_disk, so the on-disk qcow2 is preserved.
        """
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before rebuild_seed")
        disk = vm_dir / "disk.qcow2"
        answers = vm_dir / "answers"
        if not disk.exists():
            raise FileNotFoundError(f"missing disk for seeded resume: {disk}")
        iso = resolve_image(cfg)
        answers.write_text(render_answers(cfg))
        (vm_dir / "state.seeded").touch()
        install = _qemu_install_args(cfg, vm_dir, disk, iso)
        runtime = _qemu_runtime_args(cfg, vm_dir, disk)
        return InstallArtifacts(
            qemu_install_args=install,
            qemu_runtime_args=runtime,
            seed_paths=[disk, answers],
        )

    def runtime_args(self, cfg: VMConfig, vm_dir: Path) -> list[str]:
        """Reconstruct runtime QEMU args without rebuilding the disk.

        Used by `uqmm start` — calling build() again would `qemu-img create`
        a fresh blank disk and lose the installed system.
        """
        if cfg.ssh_port is None:
            raise ValueError("ssh_port must be resolved before runtime_args")
        disk = vm_dir / "disk.qcow2"
        if not disk.exists():
            raise FileNotFoundError(f"missing runtime artifact: {disk}")
        return _qemu_runtime_args(cfg, vm_dir, disk)


def _common_args(cfg: VMConfig, vm_dir: Path, *, smp: int, mem_mb: int) -> list[str]:
    assert cfg.ssh_port is not None
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35",
        "-cpu",
        "max",
        "-smp",
        str(smp),
        "-m",
        str(mem_mb),
        "-nographic",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{cfg.ssh_port}-:22",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-qmp",
        f"unix:{vm_dir / 'qmp.sock'},server=on,wait=off",
    ]


def _qemu_install_args(cfg: VMConfig, vm_dir: Path, disk: Path, iso: Path) -> list[str]:
    install_smp = max(cfg.vcpus, 4)
    install_mem = max(cfg.memory_mb, 4096)
    bumps: list[str] = []
    if cfg.vcpus < 4:
        bumps.append(f"vcpus {cfg.vcpus} → {install_smp}")
    if cfg.memory_mb < 4096:
        bumps.append(f"memory {cfg.memory_mb}MB → {install_mem}MB")
    if bumps:
        print(
            f"note: alpine install raised {', '.join(bumps)};"
            " runtime will use your requested values",
            file=sys.stderr,
        )
    kernel, initrd = extract_alpine_boot_files(iso)
    return [
        *_common_args(cfg, vm_dir, smp=install_smp, mem_mb=install_mem),
        # CDROM still required — the initramfs mounts the squashfs from it.
        "-cdrom",
        str(iso),
        # Boot the extracted kernel directly so we control the cmdline.
        # Without `console=ttyS0`, getty never spawns on the serial port
        # and the pexpect driver hangs waiting for `login:`. See
        # extract_alpine_boot_files for the full rationale.
        "-kernel",
        str(kernel),
        "-initrd",
        str(initrd),
        "-append",
        "modules=loop,squashfs,sd-mod,usb-storage console=ttyS0,115200",
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-no-reboot",
        # wait=on so QEMU blocks until the pexpect driver connects — no boot
        # output is missed. reconnect-ms is a client-side option and QEMU
        # rejects it on server-listen sockets (hard error since 11.0).
        "-serial",
        f"unix:{vm_dir / 'serial.sock'},server=on,wait=on",
    ]


def _qemu_runtime_args(cfg: VMConfig, vm_dir: Path, disk: Path) -> list[str]:
    return [
        *_common_args(cfg, vm_dir, smp=cfg.vcpus, mem_mb=cfg.memory_mb),
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-serial",
        f"unix:{vm_dir / 'serial.sock'},server=on,wait=off",
    ]
