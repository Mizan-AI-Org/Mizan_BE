import axios from "axios";

export default class ApiService {
    baseUrl: string;
    timeout: number;
    axiosInstance: typeof axios;

    constructor() {
        this.baseUrl = process.env.API_BASE_URL || "http://localhost:8000";
        this.timeout = 5000;
        this.axiosInstance = axios;
    }

    async validateUser(token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/auth/agent-context/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });

            return {
                isValid: true,
                user: response.data.user,
                restaurant: response.data.restaurant
            };
        } catch (error: any) {
            console.error("[ApiService] Token validation failed:", error.message);
            return {
                isValid: false,
                error: error.message
            };
        }
    }

    async fetchUserData(userId: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/get`, {
                params: { userId },
                timeout: this.timeout,
                headers: {
                    'Content-Type': 'application/json',
                    'User-Agent': 'Lua-Skill/1.0'
                }
            });

            return {
                id: userId,
                name: response.data.args.userId || 'Unknown',
                url: response.data.url,
                status: 'success',
                timestamp: new Date().toISOString()
            };
        } catch (error: any) {
            return {
                id: userId,
                name: 'Unknown',
                url: null,
                status: 'error',
                error: error.message,
                timestamp: new Date().toISOString()
            };
        }
    }

    async createPost(title: string, content: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/post`, {
                title,
                content,
                publishedAt: new Date().toISOString()
            }, {
                timeout: this.timeout,
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            return {
                id: response.data.json.title || 'generated-id',
                title: response.data.json.title,
                status: 'created',
                url: response.data.url
            };
        } catch (error: any) {
            return {
                id: null,
                title,
                status: 'error',
                error: error.message,
                url: null
            };
        }
    }


    // Scheduling Methods

    async getStaffList(restaurantId: string, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/staff/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: { restaurant_id: restaurantId }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to fetch staff list:", error.message);
            return [];
        }
    }

    async getAssignedShifts(params: any, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/scheduling/assigned-shifts-v2/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: params
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to fetch assigned shifts:", error.message);
            throw new Error(`Failed to fetch shifts: ${error.message}`);
        }
    }

    async createAssignedShift(data: any, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/scheduling/assigned-shifts-v2/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to create shift:", error.message);
            // Return error details if available from backend
            if (error.response && error.response.data) {
                throw new Error(`Failed to create shift: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to create shift: ${error.message}`);
        }
    }

    async updateAssignedShift(shiftId: string, data: any, token: string) {
        try {
            const response = await axios.patch(`${this.baseUrl}/api/scheduling/assigned-shifts-v2/${shiftId}/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to update shift:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Failed to update shift: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to update shift: ${error.message}`);
        }
    }

    async detectConflicts(params: any, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/scheduling/assigned-shifts-v2/detect_conflicts/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: params
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to detect conflicts:", error.message);
            return { has_conflicts: false, error: error.message };
        }
    }

    async optimizeSchedule(data: any, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/scheduling/weekly-schedules-v2/optimize/`, data, {
                timeout: this.timeout * 2, // Optimization might take longer
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to optimize schedule:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Optimization failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Optimization failed: ${error.message}`);
        }
    }

    // Checklist Methods

    async getShiftChecklists(token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/checklists/shift-checklists/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to fetch shift checklists:", error.message);
            return { checklists: [], error: error.message };
        }
    }

    async createChecklistExecution(data: { template_id: string; assigned_shift_id?: string }, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/checklists/executions/`, {
                template: data.template_id,
                assigned_shift: data.assigned_shift_id
            }, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to create checklist execution:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Failed to create execution: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to create execution: ${error.message}`);
        }
    }

    async startChecklistExecution(executionId: string, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/checklists/executions/${executionId}/start/`, {}, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to start checklist execution:", error.message);
            throw new Error(`Failed to start execution: ${error.message}`);
        }
    }

    async syncChecklistResponse(executionId: string, data: any, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/checklists/executions/${executionId}/sync/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to sync checklist response:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Sync failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Sync failed: ${error.message}`);
        }
    }

    async completeChecklistExecution(executionId: string, completionNotes: string, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/checklists/executions/${executionId}/complete/`, {
                completion_notes: completionNotes
            }, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to complete checklist:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Completion failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Completion failed: ${error.message}`);
        }
    }
}