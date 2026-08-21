package gplaydl

import com.aurora.gplayapi.data.models.AuthData
import com.aurora.gplayapi.data.providers.DeviceInfoProvider
import com.aurora.gplayapi.helpers.AuthHelper
import java.util.Locale
import java.util.Properties

/**
 * Keeps the GPlayApi-specific authentication boundary in one place.
 *
 * Current Aurora Store uses AuthHelper.build(... Token.AUTH, isAnonymous=true).
 * The JVM-compatible GPlayApi transport used by this CLI exposes the equivalent
 * buildInsecure(email, authToken, locale, DeviceInfoProvider) entry point.
 * Replacing this transport with a pure-JVM extraction of GPlayApi 3.6.x should
 * only require changing this class.
 */
class GPlaySession(
    val authData: AuthData,
) {
    companion object {
        fun anonymous(
            dispenserAuth: DispenserAuth,
            properties: Properties,
            locale: Locale,
        ): GPlaySession {
            val provider = DeviceInfoProvider(properties, locale.toString())
            val authData = AuthHelper.buildInsecure(
                dispenserAuth.email,
                dispenserAuth.authToken,
                locale,
                provider,
            )
            return GPlaySession(authData)
        }
    }
}
