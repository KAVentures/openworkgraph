# Swedish / EU OpenWorkGraph deployment checklist

> Operational checklist only; not legal advice.

## Governance before rollout

- [ ] Named legal entity/data controller identified.
- [ ] Specific purposes documented; incompatible secondary uses prohibited.
- [ ] Lawful basis assessed for each purpose.
- [ ] DPIA requirement assessed and, where required, DPIA completed before processing begins.
- [ ] DPO/privacy counsel involved where appropriate.
- [ ] MBL/collective-agreement consultation obligations assessed before the relevant decision.
- [ ] Employee transparency notice finalized and delivery timing defined.
- [ ] Pilot scope, duration, owners and success/stop criteria documented.

## Data minimization

- [ ] Only required endpoint sensors enabled.
- [ ] Private/sensitive applications and browser hosts excluded where feasible.
- [ ] Standard boundary confirmed: no typed text, clipboard contents, ordinary key identities or screenshot bytes sent through the Gateway path.
- [ ] Aggregate-only access used where individual traces are unnecessary.
- [ ] Organization-wide raw trace access disabled or explicitly justified.
- [ ] Pseudonymous data treated as personal data, not anonymous data.

## Access/security

- [ ] Customer-controlled TLS/reverse proxy/network controls configured.
- [ ] Admin/enrollment/service secrets stored in an approved secret manager.
- [ ] Human interactive access uses organization SSO/OIDC where enabled.
- [ ] Team/group mappings reviewed for least privilege.
- [ ] Machine integrations use the minimum required scopes.
- [ ] Rate limits / edge controls configured for the deployment scale.
- [ ] Device and token revocation procedure tested.
- [ ] Access/audit logs reviewed by an assigned owner.

## Retention/deletion

- [ ] Gateway retention period tied to the stated purpose.
- [ ] Local endpoint retention policy decided separately.
- [ ] Actor/time/session deletion procedure tested.
- [ ] Backup retention and deletion/expiry behavior documented.
- [ ] Process for departing employees defined.
- [ ] Process for retroactive deletion after exclusion/privacy-policy changes defined.

## Employee transparency

- [ ] Workers know what is collected and what is deliberately not collected.
- [ ] Workers know the purposes and legal basis.
- [ ] Workers know who can see individual-level evidence and under what conditions.
- [ ] Workers know retention periods/criteria.
- [ ] Workers know how to exercise rights or raise concerns.
- [ ] New purposes/data categories require advance review and updated information.

## Go-live decision

- [ ] Security test passed.
- [ ] Restore/backup test passed.
- [ ] Privacy controls verified in the actual configuration.
- [ ] Worker consultation/negotiation obligations completed where applicable.
- [ ] Named owner authorized production rollout.
- [ ] Review date scheduled.

## Swedish references

- IMY employee-data principles: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/grundlaggande-principer/
- IMY employee monitoring: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/kontroll-och-overvakning-av-anstallda/
- IMY employee information: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/information-till-anstallda/
- IMY DPIA guidance: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/konsekvensbedomning/
- MBL: https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/lag-1976580-om-medbestammande-i-arbetslivet_sfs-1976-580/
