package org.example.gate

import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.security.config.annotation.web.reactive.EnableWebFluxSecurity
import org.springframework.security.config.web.server.ServerHttpSecurity
import org.springframework.security.web.server.SecurityWebFilterChain

// 인증은 아직 없음: 게이트웨이를 통과하는 모든 요청 허용
@Configuration
@EnableWebFluxSecurity
class SecurityConfig {

	@Bean
	fun securityWebFilterChain(http: ServerHttpSecurity): SecurityWebFilterChain =
		http
			.csrf { it.disable() }
			.authorizeExchange { it.anyExchange().permitAll() }
			.build()
}
