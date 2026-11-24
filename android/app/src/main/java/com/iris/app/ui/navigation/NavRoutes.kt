package com.iris.app.ui.navigation

import android.net.Uri

sealed class NavRoute(val route: String) {
    object Gallery : NavRoute("gallery")
    object Search : NavRoute("search")
    object Persons : NavRoute("persons")
    object PersonMedia : NavRoute("person_media/{personId}?name={personName}") {
        fun createRoute(personId: Int, personName: String): String {
            val encodedName = routeArgument(personName)
            return "person_media/$personId?name=$encodedName"
        }
    }
    object Collections : NavRoute("collections")
    object CollectionMedia : NavRoute("collection_media/{collectionId}?name={collectionName}") {
        fun createRoute(collectionId: Int, collectionName: String): String {
            val encodedName = routeArgument(collectionName)
            return "collection_media/$collectionId?name=$encodedName"
        }
    }
    object Detail : NavRoute("detail/{recordIndex}") {
        fun createRoute(recordIndex: Int): String = "detail/$recordIndex"
    }
    object LocalMediaDetail : NavRoute("local_media/{mediaUri}") {
        fun createRoute(mediaUri: String): String = "local_media/${Uri.encode(mediaUri)}"
    }
    object Spaces : NavRoute("spaces")
    object Space : NavRoute("space/{spaceId}?name={spaceName}") {
        fun createRoute(spaceId: Int, spaceName: String): String {
            val encodedName = routeArgument(spaceName)
            return "space/$spaceId?name=$encodedName"
        }
    }
    object Sync : NavRoute("sync")
    object Settings : NavRoute("settings")
    object Trash : NavRoute("trash")
    object Documents : NavRoute("documents")
    object DocumentKind : NavRoute("documents/{kind}?name={name}") {
        fun createRoute(kind: String, name: String): String =
            "documents/${routeArgument(kind)}?name=${routeArgument(name)}"
    }
    /** The backup screen opened straight on the phone's folders. */
    object DeviceFolders : NavRoute("device_folders")
}

/**
 * A value for a route's query argument. Navigation decodes arguments as URI
 * components, where "+" is a plus sign: URLEncoder's form encoding turned
 * "Ana Maria" into "Ana+Maria" on the screen. Spaces become %20 instead, and a
 * real "+" is escaped.
 */
internal fun routeArgument(value: String): String =
    java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20")
