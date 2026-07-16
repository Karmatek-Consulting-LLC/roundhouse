# Trivy ignore policy for Roundhouse-built MCP server images.
#
# Use with:  trivy image --ignore-policy deploy/trivy/ignore-policy.rego <image>
#
# NOTE: Harbor's built-in Trivy scanner does NOT read this file — Harbor only
# supports CVE-ID allowlists per project. This policy is for Roundhouse's own
# CI / local scans. The durable fix for the noise below is a minimal base image
# (e.g. the DHI Alpine variant, which ships no linux-libc-dev and scans clean).

package trivy

default ignore = false

# linux-libc-dev ships only Linux kernel *header* files, not a running kernel.
# Containers use the host's kernel, so kernel CVEs matched against this package
# are not exploitable through the image. On the Debian base this single package
# accounts for ~77% of all findings (the entire kernel CVE feed), none of which
# have a fix. Drop it as non-applicable.
ignore {
	input.PkgName == "linux-libc-dev"
}
