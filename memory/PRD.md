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

## Iteración 4 — Filtro estado Comparativa + limpieza UnitQuery (18 Feb 2026)
- **Comparativa**: el selector "Mostrar" ahora ofrece 6 estados granulares: *Sólo con diferencias*, *Todas las facturas*, *Match (coinciden)*, *Con discrepancias*, *Sólo en SII*, *Sólo en Comercial*. Se eliminó el estado redundante `onlyDiffs` y se cableó directamente `filtroEstado` al `Select`, manteniendo el cableado existente con `params.estado` en `/api/comparativa` y `/api/comparativa/export`.
- **Bug crítico backend**: en `_comparativa_data` (router_facturas.py) el bucle reasignaba la variable `estado` (parámetro de la función) por cada fila procesada, lo que desactivaba silenciosamente el filtro de estado pedido por el usuario (e.g. `?estado=coincide` devolvía discrepancias). Renombrado a `row_estado`. Verificado con curl: `coincide`, `discrepancia`, `solo_sii`, `solo_comercial` ahora devuelven sólo filas del estado correcto.
- **UnitQuery**: removidos los campos "NIF emisor" y "Nombre emisor" del formulario porque en `ConsultaLRFacturasEmitidas` el emisor es implícito (= titular). Los campos se auto-pueblan desde `nif_titular`/`nombre_titular` al construir el payload, manteniendo intacto el contrato del backend.

## Iteración 5 — Performance Comparativa con 1.28M facturas SII (18 Feb 2026)
- **Problema**: con 1.28M facturas SII en BD el endpoint `/api/comparativa` tardaba 17s y `/api/comparativa/periodos` 28s → 502 Bad Gateway intermitentes del ingress.
- **Fix índices**: añadidos `ejercicio_1_periodo_1` (compuesto) en `facturas_sii` y `facturas_comercial`. Ejecutado al arranque (idempotente).
- **Fix `/comparativa/periodos`**: sustituido `distinct()` (collection scan) por `aggregate $group` apoyado en el índice compuesto. 28s → 1.2s (24x más rápido).
- **Fix `/comparativa`**: reescrito el handler para construir resultados desde el universo comercial (siempre pequeño), cargando SII docs sólo cuando `num_serie ∈ comercial` (uses unique index). Para el estado `solo_sii` (potencialmente millones) se pagina a nivel BD con `skip/limit`. 17s → 1.7-2.9s. La función legacy `_comparativa_data` queda para `/comparativa/export` (full dump).
- **Helper `_build_filtros`**: centraliza la construcción de filtros Mongo y la restricción del universo SII a (ejercicio, periodo) presentes en comercial cuando no hay filtro explícito.
- **Cambio sutil de semántica**: cuando filtras "Sólo con diferencias" (default), `total` ahora cuenta sólo *lo accionable* (discrepancias + solo_comercial = 168), NO los 1.28M `solo_sii` (que serían facturas correctamente reportadas y no requieren acción). El usuario puede ver el universo `solo_sii` seleccionando explícitamente ese filtro.

## Iteración 6 — Soporte formato SIGLO + retry CLI (18 Feb 2026)

## Iteración 7 — Fix contador "Todas las facturas" (18 Feb 2026)
- **Problema reportado**: el usuario veía 1.282.182 en "Todas las facturas" cuando la BD tenía 1.290.015 SII + 9.220 comercial. Le faltaban 10.006 facturas.
- **Causa**: optimización de Iteración 5 acotaba el universo SII a los `(ejercicio, periodo)` presentes en `facturas_comercial` cuando no se filtraba explícitamente. Como comercial sólo tenía datos de 2026/05, las 10.006 facturas SII de los periodos 2026/01 y 2026/02 quedaban excluidas del total.
- **Fix**: eliminada la restricción implícita en `_build_filtros`. Ahora "Todas las facturas" muestra literalmente todas (1.292.188 = 7.047 + 1.282.968 + 2.173). Los índices `num_serie_factura` (unique) y `ejercicio_1_periodo_1` mantienen las consultas en ~1s.
- **Parser tabular multiformato**: refactor de `_parsear_sap_report` → `_parsear_report_tabular(text, origen)` con catálogo `_FORMATOS_TABULARES` que define la firma de cabeceras y los alias de columnas por origen. Detector `_detectar_formato_tabular(text)` devuelve `"SAP"`, `"SIGLO"` o `None`. Las funciones legacy `_parsear_sap_report` y `_detectar_sap_report` se mantienen como aliases retrocompatibles.

## Iteración 8 — Configuración de comparativa (18 Feb 2026)
- **Backend**: nueva colección `comparativa_config` (single doc) + endpoints `GET /api/comparativa/config` y `PUT /api/comparativa/config`. Helper `_load_comparativa_config()` cacheado a llamada. `diff_facturas(a, b, config)` ahora acepta:
  - `campos_comparados`: lista de campos canónicos a incluir en el diff (excluye `razon_social`, `descripcion_operacion`, etc. que NO aparecen en los ficheros comerciales).
  - `invertir_signo_por_origen`: dict `{ "SAP": bool, "SIGLO": bool }` que multiplica los importes del comercial por −1 antes de comparar (notas de crédito en negativo vs SII en positivo).
- Defaults: `["fecha_expedicion","ejercicio","periodo","base_imponible","tipo_impositivo","cuota_repercutida","importe_total"]`. Sin invertir signos.
- Propagación: `_comparativa_data`, `comparativa` y `comparativa_resumen_origenes` cargan la config y la pasan a `diff_facturas`.
- **Frontend**: nueva página `/configuracion` accesible desde la sidebar (icono ⚙️ Settings). Dos secciones: checkboxes de 17 campos (con `Nº serie factura` y `NIF titular` marcados como CLAVE no desactivables) + switches por origen para invertir signo. Botones Guardar / Restaurar defaults.
- **Validación lógica**: 4 casos unitarios verifican (a) sin invertir comercial=−100 ≠ SII=+100, (b) invertir SIGLO comercial=−100 → +100 = SII match, (c) invertir SAP no afecta a docs SIGLO, (d) campos no seleccionados se ignoran. UI verificada con screenshot funcional.

- **SIGLO**: cabeceras `Soc.|Doc.caus.|Nº oficial|FechaEntr|Fe.doc.or.|Fe.doc.or.|II|Tp.impos.|BaseImpon|Impto.ML` (notar `Doc.caus.` vs `Doc.causante` y `Nº oficial` vs `Nº doc.oficial` en SAP FI). Encoding latin-1, número con coma decimal y signo `-` al final, fechas `dd.mm.yyyy`, múltiples filas por factura (una por tramo IVA T6/T7) agrupadas por `num_serie_factura`.
- **Persistencia origen**: cada factura comercial almacena `origen_comercial: "SAP" | "SIGLO"` en `facturas_comercial`. El endpoint `POST /api/comercial/csv` devuelve el origen detectado.
- **UI**: badge "SAP"/"SIGLO" al lado del importe comercial en la tabla de Comparativa y en el panel de detalle. Texto de ayuda actualizado con descripción de ambos formatos. Toast tras importar incluye `formato SAP/SIGLO`.
- **Validación E2E**: fichero SIGLO real de 15.675 líneas → 9.218 facturas únicas, 0 errores, totales coincidentes con el footer del report (-530.769,69 € base / -57.739,43 € cuota). SAP FI sigue funcionando (test con 2 facturas con tramos IVA múltiples y signo negativo).
- **CLI retry + reanudación** (script `descargar_sii.py`): backoff exponencial ante errores transitorios de red (`ConnectionResetError 10054`) + state file `<config>.state.json` para reanudar exactamente desde la última `ClavePaginacion` exitosa. Flag `--from-start` ignora el state.
- **Problema**: con 1.28M facturas SII en BD el endpoint `/api/comparativa` tardaba 17s y `/api/comparativa/periodos` 28s → 502 Bad Gateway intermitentes del ingress.
- **Fix índices**: añadidos `ejercicio_1_periodo_1` (compuesto) en `facturas_sii` y `facturas_comercial`. Ejecutado al arranque (idempotente).
- **Fix `/comparativa/periodos`**: sustituido `distinct()` (collection scan) por `aggregate $group` apoyado en el índice compuesto. 28s → 1.2s (24x más rápido).
- **Fix `/comparativa`**: reescrito el handler para construir resultados desde el universo comercial (siempre pequeño), cargando SII docs sólo cuando `num_serie ∈ comercial` (uses unique index). Para el estado `solo_sii` (potencialmente millones) se pagina a nivel BD con `skip/limit`. 17s → 1.7-2.9s. La función legacy `_comparativa_data` queda para `/comparativa/export` (full dump).
- **Helper `_build_filtros`**: centraliza la construcción de filtros Mongo y la restricción del universo SII a (ejercicio, periodo) presentes en comercial cuando no hay filtro explícito.
- **Cambio sutil de semántica**: cuando filtras "Sólo con diferencias" (default), `total` ahora cuenta sólo *lo accionable* (discrepancias + solo_comercial = 168), NO los 1.28M `solo_sii` (que serían facturas correctamente reportadas y no requieren acción). El usuario puede ver el universo `solo_sii` seleccionando explícitamente ese filtro.

### Feb 2026 — Pipeline ELT Newman + ingesta directa a MongoDB
- **Problema**: el job web `/api/sii/consulta-mensual` se atraganta con 1.3M+ facturas por timeouts de Cloudflare/ingress.
- **Pipeline alternativo (Newman → CSV → Mongo)** documentado en `/app/backend/scripts/POSTMAN_README.md`:
  1. `AEAT_SII_Loop.postman_collection.json` con Newman saca las facturas a `export.txt`.
  2. `extraer_csv.py` reensambla las líneas partidas por Newman (bordes `│`, ANSI, wrap) y produce un `facturas.csv` limpio.
  3. **`ingestar_csv_a_mongo.py` (NUEVO)** carga el CSV a la colección `facturas_sii` con `bulk_write` + `upsert` por `num_serie_factura`. ~2000 docs/s en local. 100% idempotente.
- **Config JSON** (`config_ingesta.example.json`) — destino configurable: Mongo Docker local vs Mongo de Emergent cloud preview. Cualquier campo del JSON puede sobreescribirse por flag CLI.
- **Compatibilidad total**: misma colección destino que la app web (`facturas_sii`), mismo schema canónico, mismo índice único (`num_serie_factura`). Las facturas cargadas por este pipeline se marcan con `fuente_ultima: "newman_csv"` y la Comparativa las consume sin cambios.
- **Validación**: dry-run + ingesta real + reingesta (idempotencia confirmada) + verificación de tipos `float` en importes.


### Feb 2026 — Conciliación con CSV de Newman (R3)
- **Problema detectado**: el job mensual online ha perdido al menos una factura (`1NSN260500000027`) que sí aparece en la descarga vía Newman. Causa raíz (pendiente fix R1): en `_update_and_check` si `upsert_facturas_bulk` falla, se loguea y se avanza `clave_paginacion` igual, perdiendo la página entera.
- **Solución R3 implementada**: la app deja de depender exclusivamente del job online. Newman se promociona a "fuente verdad" para reconciliar.
  - `POST /api/sii/conciliar-newman` (multipart: file + nif_titular + ejercicio + periodo) → devuelve `{total_csv, total_bd, faltantes_en_bd, extra_en_bd, coinciden, errores_csv, faltantes_preview}`. No escribe.
  - `POST /api/sii/conciliar-newman/importar-faltantes` → hace `upsert_facturas_bulk` con `fuente_ultima: "conciliacion_newman"` para trazabilidad.
- **Nueva página `/conciliacion`** (`ConciliacionNewman.jsx`): uploader, filtros (NIF + ejercicio + periodo), botón Analizar y, si hay faltantes, botón Importar con confirmación. Cinco contadores (Total CSV, Total BD, Coinciden, Faltantes, Sólo en BD) y tabla de las primeras 100 faltantes.
- **Validación**: backend probado con 3 tests reales (analizar → faltantes=1, importar → insertadas=1, reanalizar → faltantes=0, idempotencia OK). Frontend renderiza y registra ruta + ítem sidebar.
- **Próximo paso (R1)**: cerrar el agujero en el job mensual — no avanzar la `clave_paginacion` si el `bulk_write` falla; reintentar y abortar el job con `last_safe_clave` para que sea resumible.


## Backlog priorizado
**P0 — Producción real**
- ~~Integración del cliente SOAP real con `zeep`/`requests` + autenticación mTLS con certificado digital (PFX/P12)~~ ✅ Hecho. Falta probar end-to-end con certificado AEAT real.

### Feb 2026 — Sistema de Autenticación + RBAC dinámico (Fase 1)
- **Auth JWT propio** (bcrypt + PyJWT, access 4h + refresh 7d, cookies HTTP-only `samesite=lax secure`). Login/Setup/Refresh/Logout + brute-force (5 intentos → lockout 15 min).
- **Modelo "sólo por invitación"**: admin crea usuario en `/admin/usuarios` → backend genera token URL-safe + emite email vía **Resend** (`re_***`) con link `/activar/{token}` (48 h, un solo uso).
- **RBAC con roles dinámicos**: colecciones `roles` (`{name, permissions:[str]}`) y `users` (`{email, name, role, status}`). Admin tiene wildcard `*`. Permisos editables en `/admin/roles` con catálogo central (`PERMISSIONS_CATALOG`).
- **Seed startup**: roles `admin` y `usuario` + admin inicial (`ADMIN_EMAIL` → email de bootstrap con link de definición de password).
- **Middleware FastAPI**: protege todas las rutas `/api/*` salvo `/api/auth/{login,logout,refresh,setup/*,forgot-password}`. Devuelve 401 si no hay cookie.
- **Frontend**: `AuthContext` (estado `undefined|null|user`) + `ProtectedRoute` con `requires=perm`. Nuevas páginas `Login`, `SetupPassword`, `AdminUsuarios`, `AdminRoles`. `Layout` filtra el sidebar por permiso, muestra usuario + logout. Axios `withCredentials: true` + interceptor que reintenta una vez tras 401 vía `/auth/refresh`.
- **CORS**: dado `allow_credentials=True`, ya no se permite `*`. Configurado por regex `https?://([a-z0-9-]+\.)*emergentagent\.com|http://localhost(:\d+)?`.
- **Validado E2E**: setup admin, login, /me, endpoint protegido con sesión, invite, logout, login → todos OK con curl. Frontend renderizado con login real y sidebar filtrado por permisos. Email a `miguelingv@gmail.com` recibido (Resend ID confirmado).

#### Backlog Fase 2/3 (no implementado todavía)
- Toggle de menú "Olvidé mi contraseña" → ya creado endpoint backend; falta página `/olvide-password` (Link existe en Login).
- Auditoría de acciones admin (quién invitó, quién deshabilitó).
- Multi-admin (varios users con rol `admin`).
- Verificación de dominio en Resend para enviar a usuarios externos al free tier.

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


### Feb 2026 — Eliminado modo MOCK por completo
- **Backend**: borrada clase `MockSIIClient`, función `get_default_mode`, helper `_resolve_mode`, función `_mock_factura_mensual` y todas las ramas `if effective_mode == "mock"`. Eliminado parámetro `mode` en todos los endpoints (`/sii/consulta-unitaria-cert`, `/sii/consulta-batch`, `/sii/consulta-mensual`, `/sii/verificar-completitud`). Quitado `sii_mode` de los modelos. Borrado `SII_MODE` de `backend/.env`.
- **Backend**: `build_client()` simplificada — sólo construye `ZeepSIIClient`. Si no hay certificado (ni en petición ni en servidor) lanza 400 con mensaje claro.
- **Frontend**: badge pasa de "Modo Mock/Real" a "WSDL v1.1 · mTLS" (verde). Filtro/columna `Modo` eliminados de `/logs`. Eliminada referencia `(real/mock)` en botones. `CertUploader` ahora distingue "Certificado propio" vs "Certificado del servidor". Dashboard quita "modo simulado" del texto descriptivo.
- **Validado**: lint OK, backend arranca, `/sii/config` ya no expone `default_mode`/`real_mode_available`. Frontend renderizado: badge correcto, 0 errores JS, sin texto "mock" en la UI.


### Feb 2026 — Módulo "Tasas Municipales" portado y validado E2E
- **Origen**: portado desde el repo externo `pdf-from-template` (rama main, GitHub del usuario).
- **Backend**: nuevo `router_tasas.py` con prefijo `/tasas-municipales` (CRUD municipios, upload CSV, generate PDFs, jobs list/detail, ZIP/PDF download, download-token JWT corto, settings SharePoint). `tasas_pdf.py` (ReportLab) y `sharepoint_client.py` (Microsoft Graph / mock local). Colecciones Mongo: `tasas_jobs`, `tasas_municipios`, `tasas_settings` (`_id="sharepoint"`), `tasas_uploads`.
- **Frontend**: nuevas páginas `/tasas-municipales/{panel,tasas,municipios,ajustes,jobs/:jobId}`. Sidebar grupo "Tasas Municipales" colapsable. Pages: `TasasPanel`, `TasasTasas` (upload+generate), `TasasMunicipios` (CRUD), `TasasSettings` (admin only, sólo SharePoint settings — eliminada la sección "Refacturación" del repo original al no aplicar), `TasasJobDetail` (preview + descarga PDF/ZIP).
- **Auth/RBAC**: permisos `tasas.view` (Panel/Municipios), `tasas.manage` (Tasas + crear/editar municipios), `tasas.admin` (Ajustes). Seed `usuario` actualizado con `tasas.view` + `tasas.manage` para encajar con la especificación. Seed ahora hace `$set` de permisos (antes `$setOnInsert`) para refrescar permisos canónicos en cada arranque.
- **Bugs arreglados en este port**:
  - `FileCsv` (Phosphor) → `FileSpreadsheet` (lucide-react).
  - Falta `formatApiError` en `lib/api.js`: añadido alias `formatApiError = formatApiErrorDetail`.
  - Sección "Refacturación / API externa" eliminada de `TasasSettings.jsx` (endpoint `/settings/refacturacion` inexistente, no formaba parte del scope).
  - `TasasPanel` y `TasasTasas` enlazaban a `/trabajo/:id` (legacy migtool) → corregido a `/tasas-municipales/jobs/:id`.
  - `TasasJobDetail` leía `useParams().id` pero la ruta es `:jobId` → corregido a `const { jobId: id } = useParams()`.
  - `TasasMunicipios`: añadidos `name="codigo"` / `name="nombre"` para accesibilidad.
- **Testing**: pytest backend `/app/backend/tests/test_tasas_api.py` con 17 tests (CRUD municipios, upload, generate, jobs, downloads, settings, RBAC) → 17/17 PASS. Frontend validado por testing agent + smoke tests propios: login, sidebar, Panel KPIs, Tasas upload+generate (CSV → 2 PDFs), Municipios listing, Ajustes (sólo admin), Job Detail (preview, descarga).

### Feb 2026 — Bug fix: Newman CSV wrap perdía el delimitador `|`
- **Síntoma reportado**: tras importar el CSV de Newman, multiples facturas mostraban `cuota_repercutida=null`, `tipo_impositivo` con valores no estándar (24.47), `contraparte_nif` con valores tipo nombre (`"Emiliano Morales Benito"`), `contraparte_nombre` concatenado con estado SII (`"DIAZ FRAILE JAVIERCorrecta"`), y `num_registro_presentacion` con timestamp terminando en `'`. Se reproducía como falso "Coincide" en la Comparativa cuando re-consultar unitariamente la misma factura traía los datos correctos vía SOAP y sobrescribía.
- **Causa raíz**: en `backend/scripts/extraer_csv.py`, las regex `BORDER_LEFT_RE` y `BORDER_RIGHT_RE` incluían el pipe ASCII `|` como carácter de borde de tabla Newman. Pero ese pipe **es el delimitador de columnas** dentro de `CSVHEAD:`/`CSVROW:`. Cuando un wrap rompía la línea justo después de un `|`, ese pipe quedaba al borde y se eliminaba al limpiar. Al reensamblar, los valores de las celdas adyacentes quedaban pegados (`116.54` + `21` → `116.5421`) y todos los campos posteriores se desplazaban una posición a la derecha. El punto exacto del wrap variaba por fila, así que el desplazamiento afectaba distintos campos.
- **Fix**: regex de bordes solo incluyen whitespace y `│` (U+2502, el verdadero borde Newman); ya no incluyen `|` ASCII. Cambio quirúrgico en `scripts/extraer_csv.py`.
- **Tests**: nuevo `tests/test_extraer_csv_newman_wrap.py` con 3 casos (preservación del delimitador, no-desplazamiento de columnas posteriores, no-pérdida del primer pipe de una continuación) → 3/3 PASS. 49 tests pytest pre-existentes siguen verdes.
- **Endpoint diagnóstico nuevo**: `POST /api/facturas/sii/diagnosticar-newman-wrap?aplicar=false|true` — detecta facturas con la signatura del bug (`num_registro_presentacion` terminando en `'`), devuelve muestra y opcionalmente sanea (separa nombre+estado concatenados, mueve csv_aeat-numérico desde estado_factura, recupera timestamp_presentacion). Requiere permiso `conciliacion.import`.
- **BD saneada**: borradas 256 480 facturas con `fuente_ultima: 'conciliacion_newman'` (corruptas) + saneadas 3 con campos fantasma de Newman previo. Quedan 2 790 000 facturas y 0 con signatura del bug. El usuario re-importará desde los CSV regenerados con el `extraer_csv.py` corregido.

### Backlog actual
- **P1** Soporte SII `ConsultaLRFacturasRecibidas` (facturas recibidas): UI, backend, XML mapping.
- **P1** Fase 2 Auth/RBAC: panel admin UI para crear/editar usuarios y asignar roles dinámicamente.
- **P2** Componetizar `Comparativa.jsx` (archivo enorme, 1 590 líneas).
- **P2** Alinear estilos de páginas Tasas con patrón Shadcn UI del resto.
- **P2** Verificación de dominio en Resend para invitaciones a usuarios externos.
