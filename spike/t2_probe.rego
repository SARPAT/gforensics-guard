# CP0 T2 probe: does ArmorIQ's OPA input contain the tool call's params?
# Only fires when the string "node-99" appears ANYWHERE in the input, so normal
# calls are unaffected. If it fires, the reason shows WHERE it was found.
package armoriq.authz

import rego.v1

default allow := true

hits contains sprintf("%v", [path]) if {
	walk(input, [path, value])
	value == "node-99"
}

allow := false if count(hits) > 0

deny contains msg if {
	count(hits) > 0
	msg := sprintf("cp0-t2 saw node-99 at %v; input keys %v", [hits, object.keys(input)])
}
