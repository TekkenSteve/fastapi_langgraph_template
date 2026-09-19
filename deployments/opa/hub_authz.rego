# Hub authorization policy — drop-in equivalent of LocalPolicyEngine.
# Extend this document (org sharing, teams, approval gates) without code
# changes; the engine only reads `allow` and `filter`.
package hub.authz

import rego.v1

default allow := false

allow if {
	is_owner
}

allow if {
	is_admin
}

is_owner if {
	input.resource.owner_id == input.subject.identity
}

is_admin if {
	"admin" in input.subject.permissions
}

# List/search narrowing, mirroring AccessFilter.
filter := {"allow_all": true, "owner_id": null, "object_ids": null} if {
	is_admin
}

filter := {"allow_all": false, "owner_id": input.subject.identity, "object_ids": null} if {
	not is_admin
}
