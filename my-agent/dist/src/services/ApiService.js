import axios from "axios";
export default class ApiService {
    constructor() {
        this.baseUrl = process.env.API_BASE_URL || "http://localhost:8000";
        this.timeout = 5000;
        this.axiosInstance = axios;
    }
    async validateUser(token) {
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
        }
        catch (error) {
            console.error("[ApiService] Token validation failed:", error.message);
            return {
                isValid: false,
                error: error.message
            };
        }
    }
    async fetchUserData(userId) {
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
        }
        catch (error) {
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
    async createPost(title, content) {
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
        }
        catch (error) {
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
    async getStaffList(restaurantId, token) {
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
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch staff list:", error.message);
            return [];
        }
    }
    async getAssignedShifts(params, token) {
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
        }
        catch (error) {
            console.error("[ApiService] Failed to fetch assigned shifts:", error.message);
            throw new Error(`Failed to fetch shifts: ${error.message}`);
        }
    }
    async createAssignedShift(data, token) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/scheduling/assigned-shifts-v2/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to create shift:", error.message);
            // Return error details if available from backend
            if (error.response && error.response.data) {
                throw new Error(`Failed to create shift: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to create shift: ${error.message}`);
        }
    }
    async updateAssignedShift(shiftId, data, token) {
        try {
            const response = await axios.patch(`${this.baseUrl}/api/scheduling/assigned-shifts-v2/${shiftId}/`, data, {
                timeout: this.timeout,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to update shift:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Failed to update shift: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Failed to update shift: ${error.message}`);
        }
    }
    async detectConflicts(params, token) {
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
        }
        catch (error) {
            console.error("[ApiService] Failed to detect conflicts:", error.message);
            return { has_conflicts: false, error: error.message };
        }
    }
    async optimizeSchedule(data, token) {
        try {
            const response = await axios.post(`${this.baseUrl}/api/scheduling/weekly-schedules-v2/optimize/`, data, {
                timeout: this.timeout * 2, // Optimization might take longer
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            return response.data;
        }
        catch (error) {
            console.error("[ApiService] Failed to optimize schedule:", error.message);
            if (error.response && error.response.data) {
                throw new Error(`Optimization failed: ${JSON.stringify(error.response.data)}`);
            }
            throw new Error(`Optimization failed: ${error.message}`);
        }
    }
}
