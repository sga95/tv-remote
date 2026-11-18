# Outbound rule: what the remote cannot talk to

Public version. The TV's address is replaced by `TV_ADDRESS`; no other private value appears.

The traffic capture ([capture-2026-11-18.md](capture-2026-11-18.md)) is a photograph of ten minutes. This rule is the standing guarantee: on the machine that runs the remote, the firewall refuses every packet the remote's user tries to send anywhere except back to its clients, to the machine itself and to the TV. It holds whatever the code does, in this version and in later ones, and it counts every refused attempt.

## The rule

The remote and the adb server it starts run as one ordinary user (never root). nftables can match packets by the user that owns the socket. Four lines in the output chain, shown as loaded (`1000` is that user's numeric id, from `id -u <user>`; `5555` is the TV's adb port):

```
chain output {
    type filter hook output priority filter; policy accept;
    meta skuid 1000 oifname "lo" accept
    meta skuid 1000 ct state established,related accept
    meta skuid 1000 ip daddr TV_ADDRESS tcp dport 5555 accept
    meta skuid 1000 counter drop comment "tv-remote: outbound attempt refused"
}
```

In words: that user may talk to the machine itself (adb's local plumbing), may answer connections that others opened to it (the phone on the remote's port), may open a connection to the TV's adb port, and nothing else. The chain's policy stays `accept`, so the rest of the machine (name lookups, clock, system updates, which run as other users) is not touched.

## The test, 18 November 2026

| Step | Result |
| --- | --- |
| Counter of the last line before the test | 0 packets, 0 bytes |
| As the remote's user, one deliberate download from a public address (`wget -T 5`) | "download timed out", exit code 1 |
| Counter after | 5 packets, 300 bytes |
| Control: the same kind of download as root, from the system's package mirror | works, exit code 0 |
| The remote's port from another home device | answers |
| Name lookups on the machine | answer |
| adb from the remote's user to the TV | TV listed as connected |
| The owner uses the remote from the phone | works |

The 5 packets are the deliberate attempt and its retries. From here on, any other number is an attempt by something running as that user to leave the home network, and it was stopped.

Read the counter at any time: `nft list chain inet filter output`.

## What this shows

On that machine, nothing running as the remote's user can send a packet to the internet or to another home device on its own initiative, whatever the code says. The counter tells whether anything ever tried.

## What this does not show

- Anything on a computer where the rule is not loaded. On Windows there is no equivalent in this guide yet.
- Anything about the TV, the phone or the router, which talk to the internet on their own.
- It was loaded and saved on a running system; the machine had not yet been restarted with it when this was written.
- It limits one user. Run nothing else as that user, and do your own admin work as another one, or those commands lose outbound access too.
- If the TV's address changes, the third line must be updated, or the remote stops reaching the TV.
