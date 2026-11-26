# Backend documentation

The rules and decisions this repository follows. Read them before changing the code.

| File | What it is |
| --- | --- |
| [adr/](adr/README.md) | Architecture Decision Records: why each technical choice was made |
| [logging.md](logging.md) | How the backend logs: format, levels, what is never logged, and where each part logs |
| [design-system.md](design-system.md) | The look of the product. The backend takes one thing from it: the burgundy every email uses (`emails/looks.py`) |

`design-system.md` and the ADRs that concern both apps are kept, identical, in the frontend repository too. The glossary (`docs/GLOSSARY.md`) is kept once, in the private workspace repository that holds both code repositories. When you change one copy, change the other.

The product-wide documents (the spec, PRD, FRD, SRS, TRD and the clickable prototype) live with the product docs, outside the code repositories.
