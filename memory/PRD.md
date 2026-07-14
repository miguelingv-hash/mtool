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

### Feb 2026 — Fix UI: filas top-level no marcaban discrepancia con desglose de IVA
- **Síntoma**: en la `Sheet` de detalle de Comparativa, cuando había discrepancia a nivel de tramos de IVA (`detalle_iva`), las filas top-level `base_imponible` / `cuota_repercutida` / `tipo_impositivo` no se renderizaban con el sombreado rojo (`bg-rose-50/40`) aunque visualmente los valores diferían (ej. SII=74.9 vs Comercial=-112.35). Reportado por el usuario sobre factura `26TEFJN000004814`.
- **Causa**: tras el refactor de comparación por tramos, `diff_facturas` excluye correctamente `base_imponible`/`tipo_impositivo`/`cuota_repercutida` de `diferencias` cuando hay desglose (la diferencia "real" se reporta en `diferencias.detalle_iva`). Pero el JSX usaba `isDiff = !!diferencias[campo]` → `false` para esos 3 campos → fila sin sombrear.
- **Fix**: 1 línea en `frontend/src/pages/Comparativa.jsx` — cuando `Array.isArray(diferencias.detalle_iva)` y el campo es uno de los 3 del desglose, marca `isDiff=true`. Mantiene la semántica del backend (la verdad canónica está en `detalle_iva`) y restaura el indicador visual.
- **Tests**: 12/12 pytest verdes (sin cambios backend).

### Feb 2026 — Admin Mantenimiento: vaciar módulo SII desde la UI
- **Endpoint nuevo**: `POST /api/admin/sii/vaciar-modulo` con `?dry_run=true|false` y body `{"confirmacion": "VACIAR"}` literal. Borra `facturas_sii` + `facturas_comercial` + `consultas` + `jobs`. No toca auth, comparativa_config ni tasas_*. Requiere permiso `sii.wipe` (añadido al catálogo; admin lo cubre con wildcard).
- **Página nueva**: `/admin/mantenimiento` (`AdminMantenimiento.jsx`). Tarjeta destructiva con: botón "Ver estado actual" (dry-run), botón "Vaciar módulo SII" que abre AlertDialog con input que exige escribir literalmente `VACIAR` para habilitar la acción. Tras vaciar muestra una tabla con antes/borrados/después por colección. Tests data-testid presentes (`sii-wipe-resumen`, `sii-wipe-confirm-input`, etc.).
- **Sidebar**: nuevo enlace "Mantenimiento" (icono Wrench, perm `sii.wipe`) bajo el bloque admin.
- **Validado**: endpoint probado con datos dummy (100+50+10+5) → wipe correcto (todos a 0). Rechaza confirmaciones inválidas (`"borrar"` → 400). Ruta `/admin/mantenimiento` protegida correctamente (redirige a /login sin sesión).

### Feb 2026 — Resumen de conciliación: totales agregados SII vs Σ Comercial
- **Endpoint backend** `GET /api/comparativa/totales?ejercicio=&periodo=&num_serie=` — agrega Base Imponible + Cuota IVA del universo SII y desglosa Comercial por `origen_comercial` (SAP, SIGLO, …). Aplica inversión de signo según `comparativa_config.invertir_signo_por_origen`. Usa `detalle_iva` cuando existe; cae a top-level si no. Ignora `only_diffs` por diseño (los totales reflejan la masa fiscal completa).
- Devuelve: `sii` / `comercial_por_origen` (por origen, con flag `invertido`) / `comercial_total` / `diferencias` (base, cuota, `pct_conciliado_base/cuota`).
- **Componente frontend** `ResumenTotales.jsx` (~250 líneas) — tarjeta colocada justo encima de la tabla de la Comparativa. Reacciona a los mismos filtros (`ejercicio`/`periodo`/`num_serie`). Estructura:
  - Banner KPI: verde 100% conciliado / ámbar `X% conciliado` + Δ Base y Δ Cuota.
  - Grid de 4 columnas: SII (Datos AEAT) · SAP FI (Signo invertido) · SIGLO (Signo directo) · Σ Comercial (Suma orígenes).
  - Cada columna muestra base + cuota + nº facturas. La columna Σ Comercial pinta fondo rojo y badge "Diferencia vs SII" cuando hay desviación.
- **Probado E2E** con datos dummy: 100% conciliado verde + escenario con discrepancia 0,01€ → banner ámbar 99,97% + columna Σ Comercial sombreada en rojo con diff visible.

### Feb 2026 — Tabla Comparativa: nueva columna "Fecha expedición" + ordenación de columnas
- **Nueva columna** "Fecha expedición" entre "Estado" e "Importe SII". Muestra `r.sii?.fecha_expedicion` (o `r.comercial?.fecha_expedicion` si la SII es null).
- **Ordenación** de columnas (excepto "Campos con diferencias" por petición expresa). Click toggle: sin orden → desc → asc → sin orden. Default al primer clic: descendente (mayor a menor). Cliquéable en: Nº factura · Estado · Fecha expedición · Importe SII · Importe comercial.
- Helper component `SortableHead` reutilizable. Estados `sortBy`/`sortDir` locales. La ordenación es client-side sobre `visibleItems`, estable, con null al final. Parser `DD-MM-YYYY → YYYYMMDD` para ordenación correcta de fechas.

### Feb 2026 — Conciliación Newman: import sin límite de 100k + chunking + progreso
- **Backend** (`/api/sii/conciliar-newman`): eliminado el cap `MAX_FALTANTES_PAYLOAD = 100000`. `faltantes_completas` devuelve TODAS las faltantes ordenadas. `faltantes_truncado` siempre `false` (campo conservado por compatibilidad).
- **Frontend** (`ConciliacionNewman.jsx`): el botón "Importar N faltantes" trocea automáticamente el envío a `/importar-lote` en chunks de 10 000 facturas. Timeout por chunk: 3 minutos. Cada chunk es secuencial (no paralelo) para preservar consistencia de la BD.
- **Progreso visible**: `Alert` con barra de progreso (`hechas / total` en %) y contador "lote X de Y". Se mantiene visible hasta que termina o falla.
- **Manejo de errores**: si un chunk falla, el `toast` y el `Alert` indican cuántas se procesaron antes del fallo (`procesado X de Y`). La operación sigue siendo idempotente, así que el usuario puede reintentar sin duplicar.
- **Dialogo de confirmación** actualizado: si hay > 10k faltantes, muestra "Se enviará en lotes de 10.000 con barra de progreso".

### Feb 2026 — Fix CRÍTICO: subida de CSVs Newman gigantes (>25MB / >100k facturas)
- **Síntoma**: tras subir CSV de 180MB / 800k facturas el botón "Analizar" mostraba todo "—" sin error, conexión colgada/timeout.
- **Causas combinadas**:
  1. `/sii/conciliar-newman` devolvía siempre `faltantes_completas` con TODOS los registros → JSON de ~300MB → ingress k8s timeout en preview.
  2. axios sin timeout/maxBodyLength → conexión colgada en preview.
  3. nginx Docker producción tenía `client_max_body_size 25m` → CSVs > 25MB rechazados (HTTP 413 silencioso).
  4. nginx Docker producción tenía `proxy_read_timeout 120s` → imports largos cortados.
- **Fixes**:
  - Backend: param `incluir_faltantes_completas` (default false). Respuesta del análisis ahora es ligera (sólo conteos + preview).
  - Frontend `analizar()`: `timeout: 10 min`, `maxBodyLength: 512MB`, `onUploadProgress` para feedback, mejor handling de errores con detalles.
  - Frontend `importarConfirmado()`: ahora usa el endpoint server-side `/importar-faltantes` (no `/importar-lote`). El CSV se sube una vez, todo el procesado y los inserts (chunks de 2.000) ocurren en backend. Sin re-postear miles de facturas al servidor.
  - Frontend: barra de progreso con dos fases visibles: "Subiendo CSV X%" → "Servidor procesando…".
  - Nginx prod: `client_max_body_size 512m` (era 25m), `proxy_read_timeout 1800s` (era 120s), `proxy_request_buffering off` para streaming de subidas grandes.

### Feb 2026 — Fix CRÍTICO: import async para evitar HTTP 502 Cloudflare con CSVs gigantes
- **Síntoma**: el import de 862.933 facturas (CSV 180MB) devolvía HTTP 502 desde Cloudflare con mensaje "The origin web server returned an invalid or incomplete response. This typically indicates the origin is overloaded or misconfigured."
- **Causa**: Cloudflare (proxy del preview Emergent) corta conexiones HTTP idle a ~100s. El backend sigue procesando pero la respuesta nunca llega al cliente.
- **Fix adicional encontrado en el camino**: `pymongo.errors.DocumentTooLarge` — el `$in` de 862k num_serie_factura excedía el límite de 16MB de BSON. Solución: trocear la query en chunks de 20.000 IDs.
- **Solución final**: nuevo endpoint async `POST /api/sii/conciliar-newman/importar-faltantes-async` que:
  1. Recibe el CSV multipart.
  2. Crea un job en `jobs` con `type: "conciliar-newman-import"`.
  3. Lanza el procesamiento con `asyncio.create_task` y devuelve `{job_id, status: "queued"}` inmediatamente (< 1s, sin riesgo CF timeout).
  4. El worker actualiza `progress.{phase, processed, total, faltantes_total, ya_en_bd}` y `status` (`queued`/`running`/`done`/`error`).
- **Frontend**: el botón "Importar" ahora encola el job y hace polling cada 2s a `/api/jobs/{id}`. Barra de progreso con 4 fases distintas: upload → parsing → matching → inserting. Muestra contador real (`processed / faltantes_total`).
- **Validado E2E** con CSV sintético de 50k filas: job encolado en < 1s, procesado en background, status `done` con `result.insertadas` correcto.

### Feb 2026 — Nuevo flag: excluir líneas comerciales con tipo_impositivo vacío o = 0
- **Petición**: en SAP FI y SIGLO hay líneas con tipo_impositivo nulo o 0 (típicamente exentas, suplidos, ajustes contables) que no deben conciliarse contra SII.
- **Backend**: nuevo campo de config `excluir_comercial_tipo_iva_cero` (bool, default `True`). Aplicado en:
  1. `/api/comparativa/totales`: las líneas filtradas no suman a los totales por origen ni al Σ comercial (los `% conciliado` reflejan ahora la base "comparable").
  2. `factura_model.diff_facturas` → `_diff_tramos`: las líneas filtradas no entran al matching → no aparecen como diff "comercial solo".
- **Frontend** `Configuracion.jsx`: nuevo toggle `Switch` "Excluir líneas comerciales con tipo_impositivo vacío o = 0" justo debajo del existente "Excluir base_imponible = 0". Persistido vía `PUT /comparativa/config`.
- **Tests**: 1 test nuevo `test_excluir_comercial_tipo_iva_cero_filtra_lineas` que prueba ambos comportamientos (flag on / off). Test pre-existente `test_cuota_null_sii_equivale_cero_comercial` actualizado para pasar el flag a `False` (mantiene su semántica original). 13/13 verdes.

### Feb 2026 — Fix completo: exclusión tipo_imp=0/null debe recalcular cabecera comercial
- **Síntoma**: usuario reportó factura `26TAAYN000009029` (SII base=7.21/cuota=1.51 vs COM SAP -7.18/-1.51 con detalle_iva de 2 líneas: tipo=21 base=-7.21 + tipo=null base=0.03) seguía mostrando "Discrepancia" con flag activo, debería ser "Coincide" tras filtrar la línea null.
- **Causa**: el fix anterior filtraba `detalle_iva` pero NO recalculaba `base_imponible` / `cuota_repercutida` a nivel cabecera del comercial. Como SII no tenía desglose, la comparación caía a cabecera y usaba el valor pre-filtrado (-7.18 = -7.21 + 0.03).
- **Fix**:
  - `factura_model.diff_facturas`: tras filtrar `b.detalle_iva`, recalcula `b.base_imponible` y `b.cuota_repercutida` con la suma del detalle filtrado (round 2 decimales).
  - `router_facturas._aplicar_exclusion_tipo_iva_cero` (nuevo helper): aplica el mismo filtro + recálculo al doc comercial que se devuelve al frontend via `/api/comparativa`. Garantiza coherencia visual: la columna Comercial del detail Sheet ahora muestra los valores recalculados.
- **Validado por testing agent (iteration_16.json)**: 3 tests E2E verdes que inyectan el escenario exacto del bug. 22 tests previos verdes (sin regresiones). Comercial.base recalculado correctamente. % conciliación 100%.

### Feb 2026 — Filtro de primer nivel `nif_titular` + fix Exportar CSV
- **Petición**: (1) Toggle visible para alternar entre las 2 sociedades en la Comparativa, sin mezclar masas fiscales. (2) Botón "Exportar a CSV" del listado de Comparativa estaba roto (al pulsar no descargaba nada).
- **Causas del export roto**:
  1. `window.location.href = ${API}/comparativa/export?...` hacía una navegación top-level que NO enviaba el header `Authorization: Bearer` → 401 en algunos contextos.
  2. El endpoint cargaba TODO en memoria (`_comparativa_data` con `find().to_list(length=None)` para 862k+4731 docs) → 60s+ de espera, 120MB de buffer, riesgo de timeout Cloudflare 524 o OOM en backend.
- **Fixes**:
  - Backend (`router_facturas.py`):
    - `_build_filtros` ahora acepta `nif_titular`. SII filtra estricto; comercial usa `$in: [nif, null]` (back-compat con 4 731 docs legacy sin NIF).
    - Endpoints `/comparativa`, `/comparativa/totales`, `/comparativa/resumen-origenes`, `/comparativa/periodos` propagan `nif_titular`.
    - Nuevo `GET /api/comparativa/nifs-titulares` → `{nifs_titulares: [...], comercial_sin_nif: N}` para construir el selector y avisar de data legacy.
    - `/comparativa/export` refactorizado a **streaming** con `async generator` que escribe filas conforme se generan (BOM + cabecera → comercial map → matches SII → cursor `solo_sii`). 180MB en 48s, sin OOM, sin timeout.
  - Frontend (`Comparativa.jsx` + `ResumenTotales.jsx`):
    - Estado `filtroNif`, `nifsDisponibles`, `comercialSinNif`, `exporting` + carga inicial de `/comparativa/nifs-titulares` con auto-selección si solo hay 1 NIF.
    - Nuevo bloque UI "Sociedad" (data-testid `nif-titular-selector`) con botones pill (`nif-toggle-{NIF}` + opcional `nif-toggle-all`).
    - Aviso amarillo "⚠ X comerciales sin NIF" cuando hay docs legacy.
    - `exportar()` reescrito: `api.get('/comparativa/export', { responseType: 'blob', timeout: 10min })` + `Blob URL` + `<a download>` + toast loading/success/error que diferencia 401 sesión expirada.
    - Botón Exportar ahora muestra `Loader2` + texto "Exportando…" durante la descarga.
- **Validado por testing agent (iteration_17.json)**: 8/8 tests pytest verdes (`test_comparativa_nif_titular_e2e.py`) + verificación E2E del flujo frontend (login → selector visible → autoselect → click export → descarga CSV via blob). Sin regresiones.

### Feb 2026 — Soc. → NIF en parser + Vaciado SELECTIVO + Sociedad con nombre
- **Parser SAP/SIGLO** ahora lee la columna `Soc.` de cada fila y mapea con un catálogo `_SOCIEDADES_DEFAULT` (4432→A95000295 TotalEnergies Clientes S.A.U., 2239→A74251836 BASER). Cada doc en `facturas_comercial` se persiste con `soc_origen`, `nif_titular` y `nombre_titular`. `Soc.` no mapeadas se cargan pero quedan sin NIF y aparecen en `errores` con `fila=-1` y motivo descriptivo.
- **Catálogo Soc→NIF editable** vía `GET/PUT /api/admin/sociedades` (seeds + overrides persistidos en `sociedades_catalogo`).
- **Backfill** ejecutado a los 5 016 docs comerciales legacy → A95000295/TotalEnergies (`comercial_sin_nif=0`).
- **Vaciado SELECTIVO en `/mantenimiento`**: 3 ámbitos — `todo`, `sii`, `comercial`.
- **Toggle Sociedad** muestra nombre + NIF.
- Validado por testing agent (iteration_18.json): 10/10 nuevos pytest + 8/8 regresión + frontend E2E.

### Feb 2026 — Fix parser comercial: variante SIGLO HC30 + Override de sociedad
- **🐛 Bug reportado**: al subir un report SIGLO HC30 real (extracto de balance de 3.2 MB, 17k líneas), la UI mostraba `0 facturas importadas · 1 error`. El parser fallaba en autodetección.
- **🔬 Causa raíz** (3 problemas apilados):
  1. **Cabecera híbrida**: SIGLO HC30 usa `Doc.caus.` (abreviatura SIGLO) PERO `Nº doc.oficial` (formato SAP). Ninguna de las 2 firmas de `_FORMATOS_TABULARES` cubría esta variante.
  2. **Substring detection frágil**: `_detectar_formato_tabular` usaba `sig in line` como substring → `Doc.caus.` era substring de `Doc.causante` y SIGLO habría matcheado falsamente ficheros SAP.
  3. **Cabecera duplicada**: los reports HC30 reinsertan la cabecera cada N líneas (paginación); el parser la trataba como fila de datos.
  4. **Bonus**: la columna `Soc.` de HC30 contiene la clase de asiento contable (`HC30`, `NC`), NO el código SAP de sociedad. El mapeo `Soc.→NIF` fallaba obligadamente.
- **🟢 Fixes aplicados** (`router_facturas.py`):
  - `_FORMATOS_TABULARES.SIGLO`: firma reducida a los 5 tokens realmente distintivos y `col_num` acepta ambos aliases (`Nº oficial`, `Nº doc.oficial`).
  - `_detectar_formato_tabular` y búsqueda de header en el parser: comparación por **tokens exactos** (`split("|") + strip`) en lugar de substring. Elimina falsos positivos y añade robustez a variantes con columnas extra.
  - Skip de cabecera duplicada: si una fila del cuerpo tiene el mismo set de cells que `header_cells`, se ignora.
  - **Nuevo parámetro `nif_titular_override`** en `POST /api/comercial/csv`: fuerza el NIF+nombre en TODAS las filas ignorando `Soc.`. Requiere que el NIF esté en el catálogo (400 si no); limpia además el aviso "Soc no mapeadas" del report de errores.
  - Bandera equivalente `--nif-titular` en `scripts/import_comercial.py`.
- **🟢 UI** (`CargaComercialCSV.jsx`): añadido `<Select>` "Forzar sociedad (opcional)" arriba del dropzone; por defecto "Auto-detectar por columna Soc.". Lista dinámica desde `/api/comparativa/nifs-titulares`.
- **🟢 Test de regresión** (`backend/tests/test_parser_siglo_hc30.py`): mini-report HC30 sintético + validación de que SAP no se detecta como SIGLO. Ambos verdes.
- **🟢 Verificación end-to-end** con el CSV real de 3.2 MB: **12 818 facturas SIGLO** parseadas, agrupación correcta de duplicados HC30/NC del mismo `num_serie_factura`, 0 errores, NIF `A95000295` forzado en todas.

### Feb 2026 — Scripts CLI de carga directa (sin HTTP) + Fix Caddy max_size + Streaming upload
- **Trío de mejoras** para resolver la carga masiva de CSVs grandes (~180 MB / 865 k facturas) que daba `ERR_BAD_REQUEST` y `ECONNABORTED` en producción.

**1. Fix inmediato del HTTP (deploy/Caddyfile + backend)**:
- `Caddyfile`: `request_body max_size 50MB → 600MB` para no rechazar uploads grandes en la capa de proxy.
- `Caddyfile`: añadidos `transport http` con `read_timeout 30m`, `write_timeout 30m`, `response_header_timeout 10m` — antes Caddy cortaba uploads lentos por idle.
- `router_facturas.conciliar_newman_importar_async`: ahora hace streaming a disco (chunks de 1 MB → `tempfile.NamedTemporaryFile`) en lugar de `await file.read()` que cargaba los 180 MB en RAM y disparaba OOM-killer en EC2 modestos.
- Wrapper `_ejecutar_importar_faltantes_job_desde_disco` con cleanup en `finally` para borrar el temporal siempre.

**2. Rotación de logs Docker (`docker-compose.yml`)**:
- YAML anchor `&default-logging` aplicado a los 3 servicios: `max-size: 30m`, `max-file: 5`. Tope ~150 MB por contenedor, ~450 MB total. Evita llenar el disco EBS.

**3. Scripts CLI de carga directa a Mongo (NUEVO — recomendado para masivos)**:
- `backend/scripts/import_newman_sii.py`: carga CSV Newman → `facturas_sii` sin HTTP.
- `backend/scripts/import_comercial.py`: carga SAP/SIGLO → `facturas_comercial` sin HTTP, con autodetección de formato y mapeo `Soc.` → `nif_titular` desde el catálogo.
- Módulo compartido `scripts/_common.py`: lock file (`/tmp/import_*.lock`), bulk_upsert por lotes con progress, `get_mongo_db()` desde env vars, cleanup CSV.
- Reutiliza los parsers oficiales del backend (`_parsear_csv_newman`, `_parsear_report_tabular`) → cero divergencia.
- Volumen montado en `docker-compose.yml`: `/home/ec2-user/data:/data` (RW para permitir borrado tras carga OK).
- Flags: `--dry-run`, `--only-faltantes`, `--keep-csv`, `--soc-override`, `--batch-size`.
- Exit codes diferenciados (0/1/2/130) para integración con cron.
- Documentación completa en `backend/scripts/CLI_IMPORTS.md` (uso, troubleshooting, ejemplo cron).

### Feb 2026 — Reorganización: "Carga de datos" + filtro Periodo Q/M multi-select
- **Nueva pantalla `/carga-datos`** con 3 tabs (Radix Tabs sincronizados con `?tab=`):
  1. **Conciliación Newman** (default — flujo principal del usuario, carga masiva)
  2. **Comercial (SAP / SIGLO)** — extraído de Comparativa a `components/CargaComercialCSV.jsx`
  3. **Consulta mensual SII** — extraído a `components/CargaMensualSII.jsx` con job recovery + polling propios
- **`ProtectedRoute`** ahora soporta `requiresAny={[...]}` (OR de permisos). `/carga-datos` exige al menos uno de: `conciliacion.view`, `conciliacion.import`, `comercial.import`, `consultas.mensual`. El rol `usuario` por defecto NO entra; el admin sí.
- **`ConciliacionNewman`** acepta prop `embedded` para suprimir su H1 cuando se renderiza dentro del tab.
- **Sidebar**:
  - Renombrado `Comparativa SII↔CSV` → `Comparativa SII`
  - Añadida entrada `Carga de datos` (con `permAny`)
  - Eliminada `Conciliación Newman` (sus contenidos ahora viven en /carga-datos)
- **Back-compat**: `/conciliacion` redirige a `/carga-datos?tab=newman` para no romper bookmarks.
- **Pantalla Comparativa limpia**: eliminados los bloques "Consulta mensual SII" y "Importar fichero comercial", el panel lateral "Jobs en background", y todo el state/handlers asociados (~430 líneas). El archivo bajó de 1 852 a ~1 230 líneas.
- **Filtro de Periodo rediseñado**:
  - Backend (`router_facturas._build_filtros` + `comparativa_resumen_origenes`): `periodo` acepta CSV ("01,02,03") → split + `$in` en Mongo. Tolera espacios.
  - Frontend Comparativa: nuevo selector con 2 líneas — `Quarter` (Q1-Q4 con tooltip de meses) y `Mes` (01-12 con label Ene-Dic). **Mutex**: marcar un quarter limpia meses y viceversa. Multi-select dentro de cada línea. Pill resumen `Filtrando: Q2 + Q3 (6 meses)` con botón `✕ limpiar`. La línea inactiva se renderiza grisada (no deshabilitada — click sustituye la otra).
- **Bug encontrado y arreglado durante self-test**: `CargaMensualSII` importaba `PERIODOS` de `@/lib/api` (array de `{value,label}`) y renderizaba los objetos directamente → React error "Objects are not valid as a React child". Sustituido por una constante local plana `["01"…"12"]`.
- **Validado por testing agent (iteration_19.json)**: 6/6 backend tests verde (periodo CSV + $in + tolerancia espacios). ~95% frontend (19/20 — el faltante era precisamente el bug del PERIODOS, ya arreglado y reverificado por screenshot).


## Implementado el 11 Jul 2026 — Audit Trail de importaciones (`imports_log`)
- **Nuevo módulo** `backend/imports_log.py` con `start_import`, `finish_import`, `add_import_errors` y `ensure_indexes`. Colección MongoDB dedicada `imports_log`.
- **Campos registrados**: `id`, `origen` (sii|comercial), `fuente` (ui_upload|conciliacion_newman[_async]|consulta_mensual_aeat|cli_newman|cli_comercial), `file_name`, `file_size_bytes`, `user_id`, `user_email`, `nif_titular`, `ejercicio`, `periodo`, `total_procesados`, `insertados`, `actualizados`, `errores_count`, `errores` (list, máx 100), `status`, `error_message`, `job_id`, `timestamp_start/end`, `duration_ms`, `extra`.
- **Endpoints instrumentados** (todos crean/cierran automáticamente el audit log):
  - `POST /api/comercial/csv` (UI)
  - `POST /api/sii/conciliar-newman/importar-faltantes` (sync)
  - `POST /api/sii/conciliar-newman/importar-faltantes-async` (async, hereda `import_id` en el job)
  - `POST /api/sii/consulta-mensual-async` (descarga AEAT)
  - CLI: `import_newman_sii.py`, `import_comercial.py`
- **Nuevos endpoints admin** (permiso `audit.view`):
  - `GET /api/admin/imports-log` — listado paginado con filtros (origen, fuente, status, user_email, nif_titular, file_name, date_from/to)
  - `GET /api/admin/imports-log/{id}` — detalle completo con errores por fila
  - `GET /api/admin/imports-log/stats/summary` — agregados por origen×status
- **Nueva UI** `/admin/imports-log` (`AdminImportsLog.jsx`): tabla con status pills, filtros completos, panel de detalle en Sheet lateral con métricas + errores por fila + metadatos JSON. Ítem en sidebar `Historial de importaciones` (icono `ClipboardList`).
- **Permiso nuevo** `audit.view` añadido al catálogo. Admin lo tiene por wildcard `*`.
- **Validado**: 3 flujos e2e via curl (upload OK, upload con errores de fila, upload que revienta con HTTP 400 — todos generan un `imports_log` correcto con su status y errores). Screenshot con 3 filas + sheet de detalle abierto verificado.

### Backlog actual
- **P1** Soporte SII `ConsultaLRFacturasRecibidas` (facturas recibidas): UI, backend, XML mapping.
- **P1** Fase 2 Auth/RBAC: panel admin UI para crear/editar usuarios y asignar roles dinámicamente.
- **P2** Centralizar `_SOCIEDADES_DEFAULT` / `_SOCIEDADES_SEED` en `backend/catalogos.py`.
- **P2** UI admin para editar el catálogo de Sociedades (Soc→NIF→Nombre).
- **P2** `useMemo(effectivePeriodo)` + `AbortController` en las 3 llamadas paralelas de Comparativa (iter19 code review).
- **P2** `CargaDatos.tab` controlado por searchParams en cada render (back/forward — iter19).
- **P2** Componetizar `Comparativa.jsx` (~1 230 líneas).
- **P2** Salvaguarda env flag `REACT_APP_ALLOW_WIPE=false` para `/mantenimiento`.
- **P2** Alinear estilos páginas Tasas con Shadcn UI.
- **P2** Verificación de dominio en Resend.

## Fix crítico 14 Jul 2026 — Escalabilidad Comparativa a 1M+ facturas
Tras subir un fichero HC30 con 1.7M líneas, aparecieron 500/502 en la Comparativa. Root causes encontrados:

- **Bug 1** — `$in` con >100k `num_serie_factura`: BSON >16MB → `DocumentTooLarge`. Solución: chunking de 20k en `_comparativa_impl` y `_comparativa_resumen_origenes_impl`.
- **Bug 2** — `nif_titular = {"$in": [nif, None]}` matcheaba las 1.5M sin nif junto con las de la sociedad filtrada. Solución: quitado el legacy `None` en 3 sitios (`_build_filtros`, `_comparativa_resumen_origenes_impl`, `_comparativa_periodos_impl`). Ahora sólo docs explícitamente etiquetados con el NIF.
- **Bug 3** — Sub-queries del bundle cargaban 1M docs con `to_list(None)` × 3 = 15GB → OOM del pod. Solución: `_comparativa_resumen_origenes_impl` refactorizada a streaming (cursor + batches de 20k). `_comparativa_totales_impl` ya usaba cursor.
- **Bug 4** — `_comparativa_impl` seguía cargando todo el universo comercial. Solución: fast-path para `estado=solo_comercial` con paginación directa en Mongo (`.skip().limit()`). No aplica sort custom ni num_serie regex.
- **Modo ligero en resumen-origenes**: cuando universo > 500k, no cruza con SII (devuelve solo agregados). El detalle matches/discrepancias se consulta con estado específico.
- **Guard anti-OOM**: `_comparativa_bundle_impl` devuelve **400 amigable** si universo > 200k sin nif o > 500k con nif y sin estado. Frontend detecta el 400 y cambia automáticamente a `estado=solo_comercial` con toast informativo.
- **Warmup ligero**: sólo precarga bundles con <300k docs y combinaciones (nif, año, mes) con <200k. Con datasets mayores el user acepta el cache-miss de la 1ª carga (~13s).
- **Catálogo sociedades**: añadidas entradas `HC39→A74251836`, `HC30→A95000295`, `NC→A95000295` para que futuros imports SIGLO mapeen el NIF automáticamente.

### Estado actual medido
- Bundle A74251836 (487k comerciales) `estado=solo_comercial`: **11.7s** cache-miss / **<200ms** cache-hit
- Bundle A95000295 (1M comerciales) `estado=solo_comercial`: **12.8s** cache-miss / **<200ms** cache-hit
- Bundle A95000295 sin estado: **400 amigable en 0.27s** → frontend cambia a solo_comercial automáticamente
