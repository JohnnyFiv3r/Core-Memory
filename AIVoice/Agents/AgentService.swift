// AgentService.swift — Protocol for agent communication
import Foundation

protocol AgentService {
    var agentName: String { get }
    func send(message: String) async throws -> String
    func startListening(onMessage: @escaping (String) -> Void)
    func stopListening()
}
