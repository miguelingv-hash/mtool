# Colección Postman · AEAT SII Facturas Emitidas

Esta carpeta contiene una colección Postman y 4 entornos para invocar
directamente el servicio SOAP `ConsultaLRFacturasEmitidas` de la AEAT
**sin pasar por nuestra aplicación**.

Sirve para aislar problemas: si una llamada que falla en la app funciona
en Postman, el problema está en nuestro código; si también falla en
Postman, el problema está en el certificado, los datos, o el
apoderamiento.

## Archivos

| Archivo | Qué es |
|---|---|
| `AEAT_SII_FacturasEmitidas.postman_collection.json` | Colección con 3 peticiones (consulta unitaria, consulta de periodo y GET de diagnóstico) |
| `Env_Preproduccion_Normal.postman_environment.json` | Entorno **pre-producción** + cert. normal (`www7.aeat.es`) |
| `Env_Preproduccion_Sello.postman_environment.json` | Entorno **pre-producción** + cert. de sello (`prewww10.aeat.es`) |
| `Env_Produccion_Normal.postman_environment.json` | Entorno **producción** + cert. normal (`www1.agenciatributaria.gob.es`) |
| `Env_Produccion_Sello.postman_environment.json` | Entorno **producción** + cert. de sello (`www10.agenciatributaria.gob.es`) |

## Importar en Postman

1. Abre Postman.
2. Botón **Import** (arriba a la izquierda).
3. Arrastra los **5 archivos JSON** a la ventana (colección + 4 entornos)
   o pulsa "Files" y selecciónalos.
4. En la esquina superior derecha, abre el desplegable **Environment** y
   elige el que corresponda a tu certificado (lo más habitual:
   *AEAT SII · Producción · cert. de sello*).

## Configurar el certificado mTLS

Sin certificado la AEAT te devolverá HTML o cerrará la conexión.

1. Postman → **Settings** (engranaje arriba a la derecha) → pestaña
   **Certificates** → **Add Certificate**.
2. Rellena el formulario:
   - **Host**: el dominio del entorno **sin protocolo ni puerto**, p.ej.
     `www10.agenciatributaria.gob.es` (necesitas una entrada por cada
     host que vayas a usar).
   - **Port**: déjalo en blanco para que use 443 por defecto.
   - **PFX file**: selecciona tu `.pfx` o `.p12` (Postman ≥ 10.x lo
     soporta directamente).
   - **Passphrase**: la contraseña del PFX.
3. Pulsa **Add**.
4. Repite el alta para cada uno de los 4 hosts si vas a probar varios
   entornos. Lo más práctico: añade el host del entorno donde realmente
   trabajas.

> Si tu Postman es antiguo y no acepta PFX, convierte primero a PEM:
> ```bash
> openssl pkcs12 -in cert.pfx -nocerts -out cert.key -nodes
> openssl pkcs12 -in cert.pfx -nokeys -out cert.crt
> ```
> y usa **CRT file** + **KEY file** en el formulario.

## Configurar los datos de la factura

Variables que verás en la pestaña **Variables** de la colección (no en
los entornos; las edita ahí porque son comunes a cualquier entorno):

| Variable | Ejemplo | Descripción |
|---|---|---|
| `nif_titular` | `B12345678` | NIF/CIF del titular del libro registro |
| `nombre_titular` | `MI EMPRESA SL` | Razón social del titular |
| `ejercicio` | `2025` | Año fiscal (YYYY) |
| `periodo` | `01` | Mes (01-12) o trimestre (1T-4T) |
| `num_serie_factura` | `F2025-001` | Serie + nº de la factura tal como se envió en el suministro |
| `fecha_expedicion` | `15-01-2025` | Fecha de expedición (DD-MM-YYYY) |

> El `host` viene del entorno seleccionado, NO se edita aquí.

## Lanzar la petición

1. Selecciona el entorno (esquina superior derecha).
2. Abre la carpeta **1 · Consulta** → **ConsultaLRFacturasEmitidas**.
3. Pulsa **Send**.

## Interpretar la respuesta

La pestaña **Tests** analiza la respuesta automáticamente y muestra el
veredicto. Casos típicos:

### ✅ SOAP válido
```
✓ HTTP 200
✓ Respuesta SOAP recibida
✓ ResultadoConsulta = Correcto
✓ EstadoRegistro detectado: Correcta
```
La AEAT te responde con el estado real de la factura. Mira el body XML
para ver `EstadoRegistro`, `NumRegistroPresentacion`, `CSV` y
`TimestampPresentacion`.

### ⚠️ La AEAT devolvió HTML (causa más habitual al fallar)
```
✗ AEAT respondió HTML (no SOAP) — diagnóstico
   Title del HTML: Acceso denegado
   Diagnóstico: ACCESO DENEGADO por la AEAT...
```
Pulsa **Console** (abajo a la izquierda) → verás el `console.log` con
el diagnóstico. Posibles causas:

| Diagnóstico | Acción |
|---|---|
| *ACCESO DENEGADO / cl_caut* | Tu certificado no está autorizado para ese NIF. Verifica que el NIF del cert coincide con `nif_titular` **o** que tienes apoderamiento dado de alta en la sede AEAT. |
| *Certificado caducado / inválido* | Renueva el certificado en la FNMT o en tu AC. |
| *Mantenimiento / fuera de servicio* | Espera; pasará en minutos. |
| HTML genérico | Probablemente el entorno está equivocado: prueba el otro selector (sello ↔ normal). |

### ❌ SOAP Fault
```
✓ SOAP Fault recibido
   faultstring: ...
```
La petición llegó al SII pero los datos son incorrectos (XML mal
formado, NIF inválido, etc.). El `faultstring` te dice exactamente qué.

### ❌ Sin respuesta / timeout
- No tienes certificado configurado para ese host (vuelve al apartado de
  configuración del cert).
- La AEAT está caída (raro).
- Firewall corporativo bloqueando 443 hacia AEAT.

## Probar el endpoint sin enviar SOAP

Carpeta **2 · Diagnóstico** → **GET endpoint (verificación TLS)**.

Hace un simple GET al endpoint. Como la AEAT solo acepta POST, te
devolverá 405 / 500 / 400, pero eso es justo lo que queremos: confirmar
que **la mTLS funciona**. Si te sale "no se puede establecer conexión"
o un timeout, todavía no tienes el certificado bien configurado en
Postman para ese host.

## Comparar con tu app

Si la misma petición SOAP que funciona aquí falla en tu app, mándame:

1. El **body raw** que se envió desde Postman (lo ves en la respuesta,
   pestaña "Request").
2. El **body raw** que se envía desde la app (visible en el detalle de
   cada consulta del histórico, pestaña *SOAP Request*).

Comparando los dos XML detectaremos la diferencia.

## Operaciones adicionales (no incluidas)

La colección solo trae **ConsultaLRFacturasEmitidas** porque es la que
estamos depurando. Si necesitas las otras operaciones del WSDL te las
añado al pedirlas:

- `SuministroLRFacturasEmitidas` (alta/modificación de factura)
- `AnulacionLRFacturasEmitidas` (anulación)
- `ConsultaLRFacturasRecibidas` (facturas recibidas)
- `SuministroLRFacturasRecibidas`
- `ConsultaLRFactInformadasCliente` (informadas por terceros)
