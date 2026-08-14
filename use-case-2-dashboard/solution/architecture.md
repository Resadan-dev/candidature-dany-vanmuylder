# Architecture — data flows

One screen, one endpoint, one direction of travel: the Dell Wyse client pulls a
Grafana dashboard over port 80; everything else happens inside the BHS VLAN.

[Diagram 1 — Data flow](diagrams/01-data-flow.md)

Solid arrows are the flows created by this proposal. Dotted arrows are the
existing upstream chain, which nothing here modifies — plus the single option
that was rejected.

| Firewall rule | Consequence for this architecture |
|---|---|
| Office → BHS: 80 / 443 / 8080 / 1433, to **specific endpoints** | The only crossing is `monitor.nce-bhs.local:80`; whitelisting that endpoint is still a change to request |
| Office → BHS: 3000 blocked | Grafana cannot be reached directly — this is the trap in the assessment, and the whole reason for the proxy |
| Office → BHS: 5672 blocked | RabbitMQ is out of the screen's reach anyway |
| BHS → Office: nothing | Everything has to work in pull mode |

## Five things to keep in mind

1. **The TV talks to one endpoint only**, on port 80. Everything else happens
   inside the BHS VLAN: no CORS, no credential on the client side.

2. **Port 3000 never leaves the BHS VLAN.** The proxy exists only to bring
   Grafana back onto a port the firewall accepts. Adding `monitor` to the list
   of allowed endpoints is still a firewall change to request.

3. **Everything is pull-based**: no connection is initiated from the BHS VLAN
   towards the Office VLAN, as the rule requires.

4. **Two sources, two roles**: the API carries the business indicators, SQL
   carries the cross-check and the freshness banner — because it still answers
   when the API is dead, and because it is the only one that can see a stopped
   tracker while the API happily keeps serving stale numbers.

5. **RabbitMQ is left untouched.** Wiring it in would create exactly the symptom
   of use case 1: events that disappear before reaching the tracker.
