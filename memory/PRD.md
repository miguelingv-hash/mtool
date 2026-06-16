# PRD — SII Consulta (Facturas Emitidas · AEAT)

## Problema original
> necesito una nueva aplicacion que consuma un servicio web SOAP de la agencia tributaria española para consultar el estado de las facturas enviadas al SII. Suministraré el WSDL. La aplicación puede consumir ese servicio de 2 maneras, unitariamente proporcionando por pantalla los datos de entrada para invocar el servicio o bien en modo batch suministrando un CSV donde cada fila son los datos de consulta de una factura de ese mismo servicio web.

## Decisiones de usuario
- WSDL: `https://sede.agenciatributaria.gob.es/static_files/Sede/Procedimiento_ayuda/G417/FicherosSuministros/V_1_1/WSDL/SuministroFactEmitidas.wsdl`
- Servicio: ConsultaLRFactEmitidas (Facturas Emitidas, SII v1.1)
- Autenticación: **modo MOCK** (sin certificado digital real, para desarrollo)
- Entornos: pre-producción **y** producción seleccionables en UI
- Persistencia: histórico completo en MongoDB
- Idioma: español

## Arquitectura
- **Backend**: FastAPI (`/app/backend/server.py`) + Motor (MongoDB async). Todos los endpoints bajo `/api`.
- **Mock SOAP**: estado de factura determinista vía `sha256(nif_emisor|num_serie_factura|fecha_expedicion)` → 65% Correcta · 20% AceptadaConErrores · 8% Anulada · 7% NoRegistrada. Genera XMLs SOAP request/response reales para que una integración futura con `zeep`/cliente SOAP real sea drop-in.
- **Frontend**: React 19 + React Router + Shadcn UI + Recharts. Tema *Swiss / High-Contrast* (Satoshi + IBM Plex Sans).
- **Persistencia**: colección Mongo `consultas` (cada registro contiene entrada, respuesta parseada, soap_request_xml, soap_response_xml, modo, batch_id).

## Personas
- Asesor fiscal / departamento de contabilidad que necesita verificar el estado de presentación de facturas en el SII (control de errores, csv AEAT, número de registro).
- Operador batch: importa mensualmente un CSV con todas las facturas emitidas y obtiene un consolidado de estados.

## Endpoints implementados (Feb 2026)
- `GET /api/` — info del servicio + URLs WSDL/endpoints
- `POST /api/sii/consulta-unitaria` — consulta una factura
- `POST /api/sii/consulta-batch` — sube CSV (multipart) y procesa todas las filas
- `GET /api/sii/consultas` — listado paginado con filtros (modo, estado, batch_id)
- `GET /api/sii/consultas/{id}` — detalle de una consulta
- `GET /api/sii/stats` — agregados para dashboard
- `GET /api/sii/csv-template` — plantilla CSV de descarga
- `GET /api/sii/batch/{batch_id}/export` — exportar resultados de lote como CSV

## UI implementada
- `/` Dashboard con tiles, gráfico de distribución y últimas consultas
- `/consulta` Formulario de consulta unitaria con panel de respuesta + sheet XML SOAP completo
- `/batch` Subida CSV, resumen y tabla de resultados con exportación
- `/historico` Listado paginado con filtros y detalle SOAP
- Selector de entorno (pre-producción / producción) persistido en `localStorage`

## Implementado el 13 Feb 2026
- Mock SOAP determinista con XMLs request/response acordes al WSDL v1.1
- Validación Pydantic estricta (NIF, fecha DD-MM-YYYY, ejercicio YYYY, períodos 01-12 + 1T-4T)
- Tests backend: 16/16 pasados (`/app/backend/tests/test_sii_api.py`)
- Tests frontend e2e: todos los flujos críticos verificados

## Iteración 2 — Switch real/mock + cert por UI (13 Feb 2026)
- Nuevo módulo `sii_client.py` con interfaz abstracta `SIIClient` y dos implementaciones:
  - `MockSIIClient` (determinista) y `ZeepSIIClient` (zeep + mTLS, PKCS#12 → PEM eager).
- Factory `build_client(mode, cert_bytes, cert_password)` con prioridad: cert en request > `mode` > `SII_MODE` env.
- Variables `.env`: `SII_MODE`, `SII_CERT_PATH`, `SII_CERT_PASSWORD` (todas opcionales; defaults seguros para desarrollo).
- Endpoints nuevos:
  - `GET /api/sii/config` — modo activo + capacidades del servidor.
  - `POST /api/sii/consulta-unitaria-cert` — multipart con certificado opcional.
  - `POST /api/sii/consulta-batch` ahora admite `certificate` + `cert_password` + `mode`.
- Modelo `ConsultaRecord` añade campo `sii_mode` ("mock" | "real") persistido.
- UI:
  - Componente `CertUploader` (toggle real + file .pfx/.p12 + password con mostrar/ocultar).
  - Hook `useSiiConfig` para leer config del backend.
  - Badge dinámico `sii-mode-badge` en header.
  - Detalle (`QueryDetailSheet`) muestra fila "Modo invocación".
- Tests: 25/25 backend pasando (16 originales + 9 nuevos en `test_sii_cert.py`). Frontend 100% verificado.

## Iteración 3 — Bug fix selector de entorno (Feb 2026)
- **Fix P0**: `Comparativa.jsx` hardcodeaba `entorno="preproduccion"` en la consulta mensual, ignorando el selector global. Ahora usa `useEnv()` igual que `UnitQuery`/`BatchQuery`. Verificado vía wslogs que los 4 endpoints (`preproduccion`, `preproduccion_sello`, `produccion`, `produccion_sello`) se mapean correctamente al endpoint AEAT esperado.

## Backlog priorizado
**P0 — Producción real**
- ~~Integración del cliente SOAP real con `zeep`/`requests` + autenticación mTLS con certificado digital (PFX/P12)~~ ✅ Hecho. Falta probar end-to-end con certificado AEAT real.
- Validación NIF/CIF con dígito de control oficial.

**P1 — Calidad de servicio**
- Reintentos automáticos con backoff ante errores transitorios del SII.
- Almacenamiento del XML cifrado en reposo y firma del request con XMLDSig.
- Roles/usuarios (auth) y separación por NIF de titular.

**P2 — Productividad**
- Programador (cron) para consultas batch periódicas + alertas por email/Slack ante facturas `NoRegistrada` o `AceptadaConErrores`.
- Soporte adicional para ConsultaLRFactRecibidas y otros libros del SII.
- Vista de comparación (envío vs. registrado) y reconciliación con ERP.

## Próximas acciones
1. Conectar cliente SOAP real cuando esté disponible el certificado.
2. Añadir gestión de usuarios y multi-empresa.
3. Programador de consultas batch + notificaciones.
