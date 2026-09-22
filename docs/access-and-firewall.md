# SSH access and UFW lab

Use a disposable Ubuntu VM and keep its console available. The installation script intentionally leaves access policy to this separate exercise. The commands below change the VM where you run them.

## 1. Establish a working key login

On your client computer, generate a key if you do not already have a suitable one:

```bash
ssh-keygen -t ed25519
ssh-copy-id your-user@VM_IP
ssh -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no your-user@VM_IP
```

Replace `your-user` and `VM_IP`. Confirm the login succeeds with your key before disabling password authentication. Keep that session open. Do not copy your private key into this repository.

On the VM, inspect the authorized-key permissions:

```bash
ls -ld ~/.ssh
ls -l ~/.ssh/authorized_keys
```

The directory should be owned by the login user with mode 0700 and the key file with mode 0600. The project service account is not an SSH login account.

## 2. Review the supplied server policy

`config/sshd/00-linux-admin.conf` enables public-key authentication and disables root login, password/keyboard-interactive authentication, agent/TCP forwarding, and X11 forwarding. It keeps the SSH port unchanged.

OpenSSH normally takes the first configured value for a setting. Existing earlier drop-ins and conditional `Match` sections can affect the outcome; inspect the effective policy for your actual login user and client address.

On the VM, from the repository directory, ensure this new filename does not already hold someone else's configuration:

```bash
sudo test ! -e /etc/ssh/sshd_config.d/00-linux-admin.conf
```

If it already exists, review/back it up before making changes. Otherwise install this project's file:

```bash
sudo install -o root -g root -m 0644 config/sshd/00-linux-admin.conf /etc/ssh/sshd_config.d/00-linux-admin.conf
sudo /usr/sbin/sshd -t
sudo /usr/sbin/sshd -T -C user=your-user,host=client,addr=CLIENT_IP
```

Replace the context values. Confirm the output includes:

```text
permitrootlogin no
pubkeyauthentication yes
passwordauthentication no
kbdinteractiveauthentication no
authenticationmethods publickey
```

Only after syntax and effective settings are correct:

```bash
sudo /usr/sbin/sshd -t && sudo systemctl reload ssh.service
```

Open a second key-based session and verify it works. If it does not, keep the original session open and inspect `sudo journalctl -u ssh.service -n 50`.

Rollback this project's drop-in from the VM console:

```bash
sudo mv /etc/ssh/sshd_config.d/00-linux-admin.conf /etc/ssh/sshd_config.d/00-linux-admin.conf.disabled
sudo /usr/sbin/sshd -t && sudo systemctl reload ssh.service
```

CI tests the supplied policy in a separate loopback-only sshd on port 22292 with temporary keys and a temporary login user. It does not replace the runner's main SSH configuration.

## 3. Preview the UFW policy

Determine the client IP as seen by the VM. Account for NAT or bridged networking; your laptop's Wi-Fi address is not always the address the VM sees.

Use a single IPv4 management host as `ADDRESS/32`, an IPv6 host as `ADDRESS/128`, or a deliberately chosen trusted subnet. The following documentation address is an example—replace it with your actual management network:

```bash
sudo bash scripts/firewall.sh --admin-cidr 192.0.2.10/32 --ssh-port 22
```

By default every UFW operation uses `--dry-run`. The policy:

1. Allows TCP SSH only from the supplied admin network.
2. Denies unsolicited incoming traffic by default.
3. Allows outgoing traffic.
4. Uses low-volume firewall logging.

No HTTP/HTTPS ports are opened because this project does not expose a web application. The helper rejects invalid ports, an unrestricted `/0` network, and, when available, an SSH client address outside the proposed network.

## 4. Apply only on the fresh lab VM

Verify the actual SSH listener and existing rules first:

```bash
sudo ss -lntp
sudo ufw status verbose
sudo ufw show added
```

From the VM console, with your real management address substituted:

```bash
sudo bash scripts/firewall.sh --admin-cidr 192.0.2.10/32 --ssh-port 22 --apply --console-confirmed
```

The helper refuses an active firewall or existing user-added rules. It does not reset or overwrite another policy. It adds the SSH allow rule before setting defaults/enabling UFW.

Open a new key-based SSH session from the allowed client. If you have a second lab client outside that network, verify it cannot connect. Existing sessions alone are not proof that a new connection is allowed.

Useful evidence:

```bash
sudo ufw status numbered
sudo ufw status verbose
sudo journalctl -k --grep=UFW -n 30
```

If access breaks, use the VM console to run `sudo ufw disable`, correct the rule, and retest. The CLI confirmation cannot verify that a console exists; you must actually have access to it.

CI validates UFW syntax in dry-run mode and unit-tests the helper's safeguards. It does not demonstrate live packet blocking; record that result after this VM exercise.

## Primary references

- [Ubuntu OpenSSH configuration](https://ubuntu.com/server/docs/how-to/security/openssh-server/)
- [Ubuntu firewall administration](https://ubuntu.com/server/docs/how-to/security/firewalls/)
