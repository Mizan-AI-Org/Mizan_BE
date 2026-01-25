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

    async getStaffProfiles(restaurantId: string, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/staff/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: {
                    restaurant_id: restaurantId,
                    include_profile: true
                }
            });
            return response.data;
        } catch (error: any) {
            const status = error.response?.status;
            const data = error.response?.data;
            console.error(`[ApiService] Failed to fetch staff profiles: ${status} ${JSON.stringify(data || error.message)}`);
            throw new Error(`API Error ${status || 'Unknown'}: ${data?.detail || data?.error || error.message}`);
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

    // Restaurant Context
    async getRestaurantDetails(restaurantId: string, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/restaurants/${restaurantId}/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to fetch restaurant details:", error.message);
            return null;
        }
    }

    // Inventory Methods
    async getInventoryItems(restaurantId: string, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/inventory/items/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: { restaurant_id: restaurantId }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to fetch inventory items:", error.message);
            return [];
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

    // Communication Methods

    async sendWhatsapp(data: { phone: string; type: 'text' | 'template'; body?: string; template_name?: string; language_code?: string; components?: any[] }, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/notifications/agent/send-whatsapp/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`, // Agent key
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to send WhatsApp:", error.message);
            if (error.response && error.response.data) {
                // Log detail if needed
                console.error(JSON.stringify(error.response.data));
            }
            return { success: false, error: error.message };
        }
    }

    async clockIn(data: { staff_id: string; latitude: number; longitude: number; timestamp?: string }, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/timeclock/agent/clock-in/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to clock in:", error.message);
            return { success: false, error: error.message };
        }
    }

    async clockOut(data: { staff_id: string; timestamp?: string }, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/timeclock/agent/clock-out/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to clock out:", error.message);
            return { success: false, error: error.message };
        }
    }

    async lookupInvitation(phone: string, token: string) {
        try {
            const response = await axios.get(`${this.baseUrl}/api/accounts/agent/lookup-invitation/`, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                params: { phone }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to lookup invitation:", error.message);
            if (error.response && error.response.data) {
                console.error(JSON.stringify(error.response.data));
            }
            return { success: false, error: error.message };
        }
    }

    async acceptInvitation(data: { invitation_token: string; phone: string; first_name: string; last_name?: string; pin: string }, token: string) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/accounts/agent/accept-invitation/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        } catch (error: any) {
            console.error("[ApiService] Failed to accept invitation:", error.message);
            if (error.response && error.response.data) {
                console.error(JSON.stringify(error.response.data));
            }
            return { success: false, error: error.message };
        }
    }
}