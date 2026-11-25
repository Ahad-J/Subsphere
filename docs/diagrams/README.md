# SubSphere diagram index

**Editable source diagrams** — render using a Mermaid-compatible renderer/editor.

| File | Diagram | Purpose |
|---|---|---|
| `01_use_cases.mmd` | Use-case flow | Actors and principal actions in both views |
| `02_high_level_modules.mmd` | High-level component/module diagram | UI, operational core, integrations, analytics plane |
| `03_low_level_modules.mmd` | Low-level module diagram | Router, authorization, domain services, UoW, repositories, event paths |
| `04_domain_class.mmd` | UML-style class diagram | Domain objects, interfaces, attributes, operations, cardinalities |
| `05_core_erd.mmd` | Entity-relationship diagram | Database entities and core associations |
| `06_usage_sequence.mmd` | Sequence | Quota enforcement, outbox, Kafka/OpenMeter, Spark |
| `07_payment_notification_sequence.mmd` | Sequence | Payment, receipt, email/WhatsApp and delivery status |
| `08_deployment.mmd` | Deployment architecture | Core and optional Compose profiles, separate Postal stack |

**Rendered visual previews:**

- [High-level architecture PNG](subsphere_hld.png) / [SVG](subsphere_hld.svg)
- [Core class diagram PNG](subsphere_class.png) / [SVG](subsphere_class.svg)
- [Main use cases PNG](subsphere_usecases.png) / [SVG](subsphere_usecases.svg)

`infra/scripts/render_diagrams.py` renders the included Graphviz visual previews. The Mermaid sources contain the richer editable diagrams. Neither the diagram files nor visual previews imply any unimplemented service already runs.
