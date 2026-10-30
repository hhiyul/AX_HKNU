package org.example.gate

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication

@SpringBootApplication
class GateApplication

fun main(args: Array<String>) {
	runApplication<GateApplication>(*args)
}
