package com.iris.app.ui.navigation

import android.net.Uri

sealed class NavRoute(val route: String) {
    object Gallery : NavRoute("gallery")
    object Search : NavRoute("search")
    object Persons : NavRoute("persons")
    object PersonMedia : NavRoute("person_media/{personId}?name={personName}") {
        fun createRoute(personId: Int, personName: String): String {
            val encodedName = java.net.URLEncoder.encode(personName, "UTF-8")
            return "person_media/$personId?name=$encodedName"
        }
    }
    object Collections : NavRoute("collections")
    object CollectionMedia : NavRoute("collection_media/{collectionId}?name={collectionName}") {
        fun createRoute(collectionId: Int, collectionName: String): String {
            val encodedName = java.net.URLEncoder.encode(collectionName, "UTF-8")
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
            val encodedName = java.net.URLEncoder.encode(spaceName, "UTF-8")
            return "space/$spaceId?name=$encodedName"
        }
    }
    object Sync : NavRoute("sync")
    object Settings : NavRoute("settings")
}
