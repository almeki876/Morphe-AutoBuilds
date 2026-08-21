package gplaydl

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URI
import java.util.Properties

data class DispenserAuth(
    val email: String,
    val authToken: String,
)

class AnonymousAuthClient(
    private val dispenserUrl: String,
    private val client: OkHttpClient = OkHttpClient(),
    private val userAgent: String = "com.aurora.store-4.8.4-76",
) {
    private val json = Json { ignoreUnknownKeys = true }

    fun login(properties: Properties): DispenserAuth {
        require(dispenserUrl.isNotBlank()) { "custom Aurora dispenser URL is required" }
        val payload = buildJsonObject {
            properties.stringPropertyNames().forEach { key ->
                put(key, properties.getProperty(key))
            }
        }
        val request = Request.Builder()
            .url(dispenserUrl)
            .header("User-Agent", userAgent)
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                // Never propagate a dispenser response body into CI logs. A
                // custom endpoint could include tokens, account details, or a
                // large Cloudflare challenge document in its error response.
                val host = runCatching { URI(dispenserUrl).host }.getOrNull()
                    ?.takeIf { it.isNotBlank() }
                    ?: "configured dispenser"
                error("Aurora dispenser failed: HTTP ${response.code} from $host")
            }
            val objectValue = json.parseToJsonElement(responseBody).jsonObject
            val email = objectValue["email"]?.jsonPrimitive?.content.orEmpty()
            val authToken = objectValue["authToken"]?.jsonPrimitive?.content.orEmpty()
            require(email.isNotBlank() && authToken.isNotBlank()) {
                "Aurora dispenser returned incomplete credentials"
            }
            return DispenserAuth(email = email, authToken = authToken)
        }
    }
}
